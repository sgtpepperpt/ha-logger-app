"""Rules + scenes from Logger (F39): this integration is Logger's hands in Home Assistant.

Only with a Logger whose `/about.features` has "rules"; the rules and scenes live in Logger
(Settings → Home Assistant), nothing is edited in HA. One *cycle* — after every successful
poll and on each webhook nudge, never two at once, never failing the poll (logged instead):

1. `GET /ha/actions` → each waiting action run in order (actions.py) → its result POSTed
   (Logger hands each action out once: none is ever run twice; a batch → one more cycle, so a
   queue longer than Logger's batch drains at once);
2. `rules_rev` changed → `GET /ha/rules?kind=from_ha` → the triggers follow (triggers.py):
   a rule whose built config is unchanged keeps its registration (a pending `for:` runs on),
   a changed one is registered again, one gone or disabled removed; a rule HA refused is
   tried again every RETRY_REFUSED_EVERY cycles;
3. `scenes_rev` changed → `GET /ha/scenes` → the `scene.logger_*` entities follow (scene.py);
4. `PUT /ha/catalog` (catalog.py) when Logger wants one or has none, the last is a day old,
   Home Assistant (re)started, or a registry change altered it (CATALOG_DEBOUNCE s quiet).

Nothing runs before Home Assistant has started: services, devices and entities are there.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any
from uuid import uuid4

from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import actions, catalog, triggers
from .api import About, LoggerClient, LoggerConnectionError, LoggerError, LoggerRefused
from .const import (
    CATALOG_DEBOUNCE,
    CATALOG_EVERY_H,
    CATALOG_RETRY,
    DOMAIN,
    FIRE_RETRY_DELAYS,
    RETRY_REFUSED_EVERY,
)
from .coordinator import LoggerConfigEntry

_LOGGER = logging.getLogger(__name__)

# Results whose POST got no answer, sent again next cycle (the action already ran; Logger
# answers 409 if it has the result already). At most this many kept.
UNSENT_MAX = 100


@dataclass(frozen=True, slots=True)
class Registration:
    """One from_ha rule's live HA trigger: its built config (`triggers.config_key`) and the
    callable that removes it."""

    key: str
    remove: CALLBACK_TYPE


class LoggerRules:
    """One Logger's rules, scenes and catalog, as seen from Home Assistant."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: LoggerConfigEntry,
        client: LoggerClient,
        about: About,
        webhook_id: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.instance_id = about.instance_id
        self.webhook_id = webhook_id
        self.webhook_registered = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._again = False
        self._started = False
        self._stopped = False
        self._cycles = 0
        self._warned: dict[str, str] = {}  # step → the refusal last warned about
        self._errors: list[str] = []
        self.last_cycle: datetime | None = None
        self.last_error: str | None = None
        self.actions_run = 0
        self._unsent: dict[int, dict[str, Any]] = {}
        # From-HA rules: the triggers registered, and those HA refused (tried again).
        self.rules_rev: Any = None
        self._rules_loaded = False
        self._registered: dict[int, Registration] = {}
        self._refused: dict[int, dict[str, Any]] = {}
        self.fired = 0
        # Logger's scenes; None until the first read.
        self.scenes: dict[int, dict[str, Any]] | None = None
        self.scenes_rev: Any = None
        self._scene_listeners: list[Callable[[], None]] = []
        # The catalog: forced at start (and by a `catalog` nudge), changed by registries.
        self._catalog_forced = True
        self._catalog_changed = False
        self._catalog_timer: CALLBACK_TYPE | None = None
        self.catalog_at: datetime | None = None
        self._catalog_digest: str | None = None
        self._catalog_retry_at: datetime | None = None
        self._catalog_failures = 0
        self.catalog_counts: dict[str, int] | None = None
        self.webhook_accepted: bool | None = None
        self.webhook_error: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────────

    @callback
    def async_start(self) -> None:
        """Listen to the registries; the first cycle once Home Assistant has started."""
        for event_type in (
            er.EVENT_ENTITY_REGISTRY_UPDATED,
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            ar.EVENT_AREA_REGISTRY_UPDATED,
            EVENT_CORE_CONFIG_UPDATE,  # HA's internal address: the webhook URL
        ):
            self.entry.async_on_unload(
                self.hass.bus.async_listen(event_type, self._registry_changed)
            )
        self.entry.async_on_unload(self._async_stop)
        self.entry.async_on_unload(async_at_started(self.hass, self._async_started))

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self.async_request_cycle()

    @callback
    def _async_stop(self) -> None:
        self._stopped = True
        if self._catalog_timer is not None:
            self._catalog_timer()
            self._catalog_timer = None
        self._remove_triggers()

    @callback
    def async_request_cycle(self, *, catalog: bool = False) -> None:
        """A cycle now (after a running one: once more); `catalog` = send it this time."""
        if catalog:
            self._catalog_forced = True
        if not self._started or self._stopped:
            return  # the start runs one; after the unload, none
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = self.entry.async_create_background_task(
            self.hass, self._async_run(), f"{DOMAIN} rules cycle"
        )

    async def _async_run(self) -> None:
        while True:
            self._again = False
            await self.async_cycle()
            if not self._again:
                return

    async def async_cycle(self) -> None:
        """One cycle; every failure logged and kept for diagnostics, never raised."""
        async with self._lock:
            self._cycles += 1
            self._errors = []
            try:
                await self._steps()
            except Exception:
                _LOGGER.exception("Logger rules: the cycle failed")
                self._errors.append("unexpected error (see the log)")
            self.last_cycle = dt_util.utcnow()
            self.last_error = "; ".join(self._errors) or None

    async def _steps(self) -> None:
        if not await self._guard("results", self._send_unsent()):
            return  # Logger isn't answering: the next poll tries again
        queue: dict[str, Any] = {}

        async def read_queue() -> None:
            queue.update(await self.client.ha_actions())

        if not await self._guard("actions", read_queue()):
            return
        if batch := queue.get("actions") or []:
            # Logger hands them out in batches: ask again until none is left.
            self._again = True
        for action in batch:
            if isinstance(action, dict):
                await self._run_action(action)
        if not self._rules_loaded or queue.get("rules_rev") != self.rules_rev:
            await self._guard("rules", self._load_rules())
        elif self._refused and self._cycles % RETRY_REFUSED_EVERY == 0:
            await self._guard("rules", self._retry_refused())
        if self.scenes is None or queue.get("scenes_rev") != self.scenes_rev:
            await self._guard("scenes", self._load_scenes())
        await self._guard("catalog", self._send_catalog(queue))

    async def _guard(self, what: str, work: Awaitable[None]) -> bool:
        """Run one step; Logger's no (or silence) noted, not raised. → whether it went.
        A refusal is a warning once, until the step works again (the catalog: until one
        is taken)."""
        try:
            await work
        except LoggerError as err:
            self._note(what, err)
            return False
        if what != "catalog":
            self._warned.pop(what, None)
        return True

    def _note(self, what: str, err: LoggerError) -> None:
        words = f"{what}: {err.words}"
        self._errors.append(words)
        if isinstance(err, LoggerRefused) and self._warned.get(what) != words:
            self._warned[what] = words
            _LOGGER.warning("Logger refused the integration's %s", words)
        else:
            _LOGGER.debug("Logger rules, %s", words)

    # ── Actions ──────────────────────────────────────────────────────────────────

    async def _run_action(self, action: dict[str, Any]) -> None:
        action_id = action.get("id")
        if not isinstance(action_id, int) or isinstance(action_id, bool):
            return
        do = action.get("do")
        try:
            words = await actions.async_run(
                self.hass, do if isinstance(do, dict) else {}
            )
        except actions.ActionFailed as err:
            result: dict[str, Any] = {"error": err.words}
            _LOGGER.debug("Logger action %s failed: %s", action_id, err.words)
        else:
            result = {"words": words}
        self.actions_run += 1
        await self._guard("results", self._send_result(action_id, result))

    async def _send_result(self, action_id: int, result: dict[str, Any]) -> None:
        try:
            await self.client.ha_action_result(action_id, **result)
        except LoggerRefused as err:
            if err.status not in (404, 409):  # gone / answered already: fine
                raise
        except LoggerConnectionError:
            if len(self._unsent) < UNSENT_MAX:
                self._unsent[action_id] = result
            raise

    async def _send_unsent(self) -> None:
        """Results Logger didn't get last time; silence again stops the cycle."""
        while self._unsent:
            action_id, result = self._unsent.popitem()
            try:
                await self._send_result(action_id, result)
            except LoggerRefused as err:
                _LOGGER.debug("Logger refused action %s's result: %s", action_id, err)

    # ── HA → Logger rules ────────────────────────────────────────────────────────

    async def _load_rules(self) -> None:
        """The rules again: only what changed is touched. A rule's registration is keyed by
        its id + its built trigger config (only enabled ones are registered at all)."""
        body = await self.client.ha_rules()
        rules = {
            r["id"]: r
            for r in body.get("rules") or []
            if isinstance(r, dict)
            and isinstance(r.get("id"), int)
            and not isinstance(r.get("id"), bool)
            and r.get("kind", "from_ha") == "from_ha"
            and r.get("enabled") is True
        }
        for rule_id, registration in list(self._registered.items()):
            rule = rules.get(rule_id)
            if rule is None or _key(rule) != registration.key:  # gone, off or changed
                registration.remove()
                del self._registered[rule_id]
        self._refused.clear()
        self._rules_loaded = True
        self.rules_rev = body.get("rev")
        for rule_id, rule in rules.items():
            if rule_id in self._registered:
                # Kept as it is; Logger told if its `problem` says otherwise.
                await self._report(rule, None)
            else:
                await self._register(rule)

    async def _retry_refused(self) -> None:
        for rule in list(self._refused.values()):
            await self._register(rule)

    async def _register(self, rule: dict[str, Any]) -> None:
        rule_id: int = rule["id"]
        error: str | None = None
        try:
            config = triggers.trigger_config(rule.get("when"))
            remove = await _shielded(
                self.hass,
                triggers.async_attach(self.hass, rule, config, self._on_fire(rule_id)),
                orphan=lambda remove: remove(),
            )
        except triggers.TriggerRefused as err:
            error = err.words
            self._refused[rule_id] = rule
        else:
            self._registered[rule_id] = Registration(
                triggers.config_key(config), remove
            )
            self._refused.pop(rule_id, None)
        await self._report(rule, error)

    async def _report(self, rule: dict[str, Any], error: str | None) -> None:
        """The registration's outcome to Logger, when it differs from the rule's `problem`.
        Never stops the other rules' registration: a failure is noted, and told again after
        the next read of the rules."""
        rule_id: int = rule["id"]
        if error == rule.get("problem"):
            return
        if error is not None:
            _LOGGER.info("Logger rule %s (%s): %s", rule_id, rule.get("name"), error)
        try:
            await self.client.ha_rule_state(rule_id, error)
        except LoggerRefused as err:
            if err.status == 404:
                self._rules_loaded = False  # deleted meanwhile: read them again
            else:
                self._note("rule state", err)
            return
        except LoggerConnectionError as err:
            self._rules_loaded = (
                False  # Logger didn't hear it: told after the next read
            )
            self._note("rule state", err)
            return
        rule["problem"] = error

    @callback
    def _remove_triggers(self) -> None:
        for registration in self._registered.values():
            registration.remove()
        self._registered.clear()

    def _on_fire(self, rule_id: int) -> Callable[..., None]:
        @callback
        def fired(
            run_variables: dict[str, Any], context: Context | None = None
        ) -> None:
            """The trigger happened: tell Logger (it runs the rule), in the background."""
            self.entry.async_create_background_task(
                self.hass,
                self._async_fire(rule_id, dt_util.utcnow().isoformat(), str(uuid4())),
                f"{DOMAIN} rule {rule_id} fired",
            )

        return fired

    async def _async_fire(self, rule_id: int, at: str, event: str) -> None:
        """`POST /ha/rules/<id>/fire`; no answer or a 5xx → the same body (same `event`:
        Logger runs it once) again after FIRE_RETRY_DELAYS, then given up."""
        said = ""
        for wait in (0, *FIRE_RETRY_DELAYS):
            if wait:
                await asyncio.sleep(wait)
            try:
                answer = await self.client.ha_rule_fire(rule_id, at, event)
            except LoggerRefused as err:
                if err.status == 404:  # deleted in Logger: read the rules again
                    self._rules_loaded = False
                    self.async_request_cycle()
                    return
                if err.status is None or err.status < 500:
                    _LOGGER.warning("Logger refused rule %s: %s", rule_id, err.words)
                    return
                said = err.words
            except LoggerConnectionError as err:
                said = err.words
            else:
                self.fired += 1
                _LOGGER.debug("Logger rule %s fired: %s", rule_id, answer)
                return
        _LOGGER.warning(
            "Logger rule %s fired at %s, but Logger didn't answer (%s): not run",
            rule_id,
            at,
            said,
        )

    @property
    def rules_registered(self) -> int:
        return len(self._registered)

    @property
    def rules_refused(self) -> int:
        return len(self._refused)

    # ── Scenes ───────────────────────────────────────────────────────────────────

    async def _load_scenes(self) -> None:
        body = await self.client.ha_scenes()
        self.scenes = {
            s["id"]: s
            for s in body.get("scenes") or []
            if isinstance(s, dict)
            and isinstance(s.get("id"), int)
            and not isinstance(s.get("id"), bool)
        }
        self.scenes_rev = body.get("rev")
        for listener in list(self._scene_listeners):
            listener()

    @callback
    def async_add_scenes_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """`listener()` after every read of Logger's scenes."""
        self._scene_listeners.append(listener)

        @callback
        def remove() -> None:
            self._scene_listeners.remove(listener)

        return remove

    # ── Catalog ──────────────────────────────────────────────────────────────────

    @callback
    def _registry_changed(self, event: Event) -> None:
        if not self._started:
            return  # the start sends one anyway
        if (entity_id := event.data.get("entity_id")) and (
            (entry := er.async_get(self.hass).async_get(entity_id)) is not None
            and entry.platform == DOMAIN
        ):
            return  # this integration's own (its scenes): never in the catalog
        if self._catalog_timer is not None:
            self._catalog_timer()
        self._catalog_timer = async_call_later(
            self.hass,
            CATALOG_DEBOUNCE,
            HassJob(self._registry_quiet, cancel_on_shutdown=True),
        )

    @callback
    def _registry_quiet(self, _now: datetime) -> None:
        self._catalog_timer = None
        self._catalog_changed = True
        self.async_request_cycle()

    async def _send_catalog(self, queue: dict[str, Any]) -> None:
        now = dt_util.utcnow()
        due = (
            self._catalog_forced
            or queue.get("catalog_wanted") is True
            or queue.get("catalog_at") is None
            or self.catalog_at is None
            or now - self.catalog_at >= timedelta(hours=CATALOG_EVERY_H)
        )
        if not (due or self._catalog_changed):
            return
        if (
            self._catalog_retry_at is not None
            and now < self._catalog_retry_at
            and not self._catalog_forced
        ):
            return  # Logger refused the last one: not every poll
        url = (
            catalog.webhook_url(self.hass, self.webhook_id)
            if self.webhook_registered
            else None
        )
        try:
            body = await _shielded(self.hass, catalog.async_build(self.hass, url))
        except Exception:
            if "catalog-build" not in self._warned:
                _LOGGER.exception("Logger rules: the catalog couldn't be built")
            self._warned["catalog-build"] = "failed"
            self._errors.append("catalog: couldn't be built (see the log)")
            self._catalog_wait(now)
            return
        self._warned.pop("catalog-build", None)
        digest = catalog.digest(body)
        if not due and digest == self._catalog_digest:
            self._catalog_changed = False  # the registries changed nothing Logger sees
            return
        try:
            answer = await self.client.ha_catalog(body)
        except LoggerRefused:
            self._catalog_wait(now)
            raise
        self._warned.pop("catalog", None)
        self._catalog_forced = self._catalog_changed = False
        self._catalog_retry_at = None
        self._catalog_failures = 0
        self._catalog_digest = digest
        self.catalog_at = now
        self.catalog_counts = {
            k: len(v) for k, v in body.items() if isinstance(v, list)
        }
        accepted = answer.get("webhook")
        error = answer.get("webhook_error")
        error = error if isinstance(error, str) and error else None
        if accepted is False and url is not None and error != self.webhook_error:
            _LOGGER.warning(
                "Logger won't nudge Home Assistant (%s): it polls Logger every scan "
                "interval instead",
                error or "webhook refused",
            )
        self.webhook_accepted = accepted if isinstance(accepted, bool) else None
        self.webhook_error = error

    def _catalog_wait(self, now: datetime) -> None:
        """Not again every poll: CATALOG_RETRY, doubling up to 6 h (a nudge skips it)."""
        self._catalog_forced = False
        self._catalog_failures += 1
        wait = min(CATALOG_RETRY * 2 ** (self._catalog_failures - 1), 6 * 3600)
        self._catalog_retry_at = now + timedelta(seconds=wait)

    # ── Diagnostics ──────────────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        url = catalog.webhook_url(self.hass, self.webhook_id)
        return {
            "webhook": {
                "registered": self.webhook_registered,
                "url": url.replace(self.webhook_id, mask(self.webhook_id))
                if url
                else None,
                "accepted_by_logger": self.webhook_accepted,
                "error": self.webhook_error,
            },
            "last_cycle": _iso(self.last_cycle),
            "last_cycle_error": self.last_error,
            "actions_run": self.actions_run,
            "results_unsent": len(self._unsent),
            "rules_rev": self.rules_rev,
            "rules_registered": self.rules_registered,
            "rules_refused": self.rules_refused,
            "rules_fired": self.fired,
            "scenes_rev": self.scenes_rev,
            "scenes": None if self.scenes is None else len(self.scenes),
            "catalog_at": _iso(self.catalog_at),
            "catalog_counts": self.catalog_counts,
        }


async def _shielded[T](
    hass: HomeAssistant,
    work: Coroutine[Any, Any, T],
    orphan: Callable[[T], None] | None = None,
) -> T:
    """`work` run to its end even when the cycle is cancelled (an unload): HA's loader
    leaves an integration it is resolving pending forever when its caller is cancelled
    midway (every later lookup of it would hang). `orphan(result)` = undo what nobody is
    left to take (a trigger registered after the unload)."""
    task = hass.async_create_task(work, eager_start=True)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:

        def done(task: asyncio.Task[T]) -> None:
            if not task.cancelled() and task.exception() is None and orphan:
                orphan(task.result())

        task.add_done_callback(done)
        raise


def _key(rule: dict[str, Any]) -> str | None:
    """The registration key a rule's `when` builds (None: it builds none — refused)."""
    try:
        return triggers.config_key(triggers.trigger_config(rule.get("when")))
    except triggers.TriggerRefused:
        return None


def mask(secret: str) -> str:
    """A webhook id in diagnostics: its first 4 characters only."""
    return f"{secret[:4]}…"


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None
