"""Logger's HTTP API, as this integration reads and writes it.

The shapes = tests/fixtures/ha_contract.json (a copy of Logger's
backend/tests/fixtures/ha_contract.json). Logger's rules for a client:

- every request names its writer: `X-Logger-Client: ha` (Logger's Audit tab, chip *ha*);
- never an `Origin` header (Logger's cross-site guard refuses another site's Origin; aiohttp
  sends none);
- a write is JSON (`Content-Type: application/json`, else 415) with `tz` in the body — the
  caller's zone decides what "today" is;
- 2xx = done (202 = queued until Nextcloud answers, still done); a refusal carries Logger's
  sentence in `error` (or `words`) and nothing was written.

The `/ha/*` routes (rules + scenes, only when `/about.features` has "rules") are the
integration's own channel: their bodies are exactly the contract's (`ha_*` keys), no `tz`.
Voice (F69, `/about.features` has "voice"): `/command/vocab` (the words Assist's sentences are
built from), `/command/parse` (a reading, nothing written), `/command/run` (`source: voice`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Any

import aiohttp
from yarl import URL

CLIENT_HEADER = "X-Logger-Client"
CLIENT = "ha"
TIMEOUT = aiohttp.ClientTimeout(total=10)
NOT_LOGGER = "this address answers, but not as Logger's API"


class LoggerError(Exception):
    """A request that got no answer, or a refusal; `words` = what to tell the user."""

    def __init__(self, words: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(words)
        self.words = words
        self.status = status
        self.body = body


class LoggerConnectionError(LoggerError):
    """No answer from Logger: network, timeout, a 5xx without Logger's words (a proxy while
    the backend restarts), or a page that isn't Logger's API (the public address's sign-in)."""


class LoggerRefused(LoggerError):
    """Logger answered no — a 4xx, a tablet that didn't wake — with its sentence: nothing
    written. `body` keeps the rest (a med tick's `already`)."""


@dataclass(frozen=True, slots=True)
class About:
    """`GET /api/about`: which Logger this is and which contract it speaks. `features` =
    what this Logger offers beyond the polled contract ("rules": rules + scenes, F39;
    "calendar": the calendar entity, F74); an older Logger sends none."""

    api: int
    instance_id: str
    code: str | None
    features: tuple[str, ...] = ()


def normalize_url(raw: str) -> str:
    """The address as typed → `scheme://host[:port][/path]`, no trailing `/` or `/api` (so
    the LAN `…:4173` and a dev proxy's `…:5173/api` both work). ValueError when it isn't an
    http(s) address."""
    text = raw.strip()
    if "://" not in text:
        text = f"http://{text}"
    url = URL(text)
    if url.scheme not in ("http", "https") or not url.host:
        raise ValueError(raw)
    path = url.path.rstrip("/").removesuffix("/api")
    return str(url.with_path(path or "/").with_query(None).with_fragment(None)).rstrip(
        "/"
    )


class LoggerClient:
    """One Logger at `url` (normalized), asked in the zone `tz`."""

    def __init__(self, session: aiohttp.ClientSession, url: str, tz: str) -> None:
        self._session = session
        self.url = url
        self.tz = tz

    async def about(self) -> About:
        body = await self._call("GET", "/about")
        api = body.get("api") if isinstance(body, dict) else None
        instance_id = body.get("instance_id") if isinstance(body, dict) else None
        if not _is_int(api) or not isinstance(instance_id, str) or not instance_id:
            raise LoggerConnectionError(NOT_LOGGER)
        code = body.get("code")
        features = body.get("features")
        return About(
            api,
            instance_id,
            code if isinstance(code, str) else None,
            tuple(f for f in features if isinstance(f, str))
            if isinstance(features, list)
            else (),
        )

    async def status(self) -> dict[str, Any]:
        return _object(await self._call("GET", "/status", params={"tz": self.tz}))

    async def quick(self) -> list[dict[str, Any]]:
        body = await self._call("GET", "/quick")
        if not isinstance(body, list):
            raise LoggerConnectionError(NOT_LOGGER)
        return [e for e in body if isinstance(e, dict) and _is_int(e.get("id"))]

    async def alerts(self) -> list[dict[str, Any]]:
        alerts = _object(await self._call("GET", "/alerts")).get("alerts")
        return (
            [a for a in alerts if isinstance(a, dict)]
            if isinstance(alerts, list)
            else []
        )

    async def fire(self, quick_id: int, **overrides: Any) -> dict[str, Any]:
        """Fire a quick entry; `overrides` = at, note, text, kcal (only what the kind takes —
        Logger refuses any other with a 400)."""
        body = {k: v for k, v in overrides.items() if v is not None}
        return _object(await self._call("POST", f"/quick/{quick_id}/fire", body=body))

    async def shopping_add(
        self,
        name: str,
        qty: str | None = None,
        shop: str | None = None,
        due: date | None = None,
    ) -> dict[str, Any]:
        """One item onto the shopping list: 201 `{ok, item}`, 202 `{…, queued: true}`."""
        body: dict[str, Any] = {"name": name, "qty": qty, "shop": shop}
        if due is not None:
            body["due"] = {"date": due.isoformat(), "time": None}
        body = {k: v for k, v in body.items() if v is not None}
        return _object(await self._call("POST", "/shopping", body=body))

    async def calendar_events(self, start: datetime, end: datetime) -> dict[str, Any]:
        """Logger's occurrences overlapping [start, end) (F74, `GET /calendar/events`, at most
        400 days apart): `{instances, events}` — each event once in `events`, by its id."""
        return _object(
            await self._call(
                "GET",
                "/calendar/events",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "tz": self.tz,
                },
            )
        )

    async def command_vocab(self) -> dict[str, Any]:
        """The words a voice sentence may hold (F69): `{foods, meals, products, lists, shops,
        usuals, symptoms, units, money: {items, presets, accounts}}`."""
        return _object(
            await self._call("GET", "/command/vocab", params={"tz": self.tz})
        )

    async def command_parse(self, text: str) -> dict[str, Any]:
        """What Logger reads in `text` — `{intent, confidence, level, eager, head, title, parts,
        words, needs, warning, alternatives}` — nothing written, no audit line."""
        return _object(
            await self._call(
                "GET", "/command/parse", params={"text": text, "tz": self.tz}
            )
        )

    async def command_run(self, text: str, intent: str | None) -> dict[str, Any]:
        """Do what `text` says, read as `intent` (the reading kept): 201 `{ok, intent, words,
        ids, path, undo, queued?}`; a refusal (400 with `needs`, a writer's 4xx) carries
        Logger's sentence in `error`."""
        body: dict[str, Any] = {"text": text, "source": "voice"}
        if intent:
            body["intent"] = intent
        return _object(await self._call("POST", "/command/run", body=body))

    async def tablet_wake(self) -> dict[str, Any]:
        """Wake the kitchen tablet: 200 `{ok, outcome, words}`; 409 not set up, 502 it didn't
        wake (both refusals with words)."""
        return _object(await self._call("POST", "/tablet/wake", body={}))

    # Rules + scenes (F39): Logger decides, Home Assistant runs. Shapes = the contract's
    # `ha_*` keys; every body is sent exactly as there (no `tz`).

    async def ha_catalog(self, catalog: dict[str, Any]) -> dict[str, Any]:
        """What HA has → `{ok, at, counts, webhook, webhook_error}`."""
        return _object(await self._call("PUT", "/ha/catalog", body=catalog, tz=False))

    async def ha_actions(self) -> dict[str, Any]:
        """The waiting actions (Logger marks them taken: each comes once) + the revs →
        `{actions, rules_rev, scenes_rev, catalog_at, catalog_wanted}`."""
        return _object(await self._call("GET", "/ha/actions"))

    async def ha_action_result(
        self, action_id: int, *, words: str | None = None, error: str | None = None
    ) -> dict[str, Any]:
        """How an action went: `{ok: true, words}` or `{ok: false, error}`; 409 = it was
        answered already, 404 = gone (both LoggerRefused)."""
        body: dict[str, Any] = (
            {"ok": False, "error": error}
            if error is not None
            else {"ok": True, "words": words}
        )
        return _object(
            await self._call(
                "POST", f"/ha/actions/{action_id}/result", body=body, tz=False
            )
        )

    async def ha_rules(self) -> dict[str, Any]:
        """The HA → Logger rules: `{rev, rules}` (`kind=from_ha`)."""
        return _object(await self._call("GET", "/ha/rules", params={"kind": "from_ha"}))

    async def ha_rule_fire(self, rule_id: int, at: str, event: str) -> dict[str, Any]:
        """A rule's HA trigger happened → `{ok, ran, skipped, words, error?}`; Logger runs
        the rule's actions itself and drops a repeated `event`. 404 = no such rule."""
        return _object(
            await self._call(
                "POST",
                f"/ha/rules/{rule_id}/fire",
                body={"at": at, "event": event},
                tz=False,
            )
        )

    async def ha_rule_state(self, rule_id: int, error: str | None) -> dict[str, Any]:
        """A rule's registration outcome: `error` = HA's refusal, None = registered."""
        return _object(
            await self._call(
                "POST", f"/ha/rules/{rule_id}/state", body={"error": error}, tz=False
            )
        )

    async def ha_scenes(self) -> dict[str, Any]:
        """Logger's scenes: `{rev, scenes}`."""
        return _object(await self._call("GET", "/ha/scenes"))

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        tz: bool = True,
    ) -> Any:
        headers = {CLIENT_HEADER: CLIENT}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps({**body, "tz": self.tz} if tz else body)
        try:
            async with self._session.request(
                method,
                f"{self.url}/api{path}",
                params=params,
                data=data,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=False,
            ) as resp:
                status = resp.status
                raw = await resp.read()
        except TimeoutError as err:
            raise LoggerConnectionError("no answer within 10 s") from err
        except aiohttp.ClientError as err:
            raise LoggerConnectionError(str(err) or type(err).__name__) from err
        payload = _decode(raw)
        if 200 <= status < 300:
            if payload is None:
                raise LoggerConnectionError(NOT_LOGGER, status)
            return payload
        if words := _words(payload):
            raise LoggerRefused(words, status, payload)
        if payload is None and status < 500:
            # A redirect or an HTML page: the public address's sign-in, another service.
            raise LoggerConnectionError(f"{NOT_LOGGER} (HTTP {status})", status)
        if status >= 500:
            raise LoggerConnectionError(f"HTTP {status}", status, payload)
        raise LoggerRefused(f"HTTP {status}", status, payload)


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _words(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error", "words"):
        if isinstance(words := payload.get(key), str) and words.strip():
            return words.strip()
    return None


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LoggerConnectionError(NOT_LOGGER)
    return payload


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
