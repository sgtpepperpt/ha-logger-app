"""One poll of Logger, and the writes every surface (buttons, actions) shares.

A poll = `GET /status?tz=` (+ `GET /alerts` with the alerts option on); `GET /quick` on the
first poll, every QUICK_EVERY-th after it, and at once when a press found an entry gone.
Logger unreachable or a 5xx → UpdateFailed → every entity unavailable until a poll answers.
Logger's null is HA's unknown, never 0: the value helpers below return None for null, for a
missing key and for a value of the wrong type alike.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import About, LoggerClient, LoggerError, LoggerRefused
from .const import (
    CONF_ALERTS,
    DEFAULT_ALERTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_FIRED,
    QUICK_EVERY,
)

_LOGGER = logging.getLogger(__name__)

type LoggerConfigEntry = ConfigEntry[LoggerCoordinator]


@dataclass(frozen=True, slots=True)
class LoggerData:
    """What one poll read: `/status`, `/quick`, `/alerts` (None with the option off)."""

    status: dict[str, Any]
    quick: list[dict[str, Any]]
    alerts: list[dict[str, Any]] | None


def dig(value: Any, *keys: str) -> Any:
    """`value[k1][k2]…`, or None where a level is missing or not an object."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def number(value: Any) -> int | float | None:
    return (
        value
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def count(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def instant(value: Any) -> datetime | None:
    """An ISO instant with its offset (Logger sends every instant with one), else None."""
    if not isinstance(value, str):
        return None
    moment = dt_util.parse_datetime(value)
    return moment if moment is not None and moment.tzinfo is not None else None


def glucose_age(data: LoggerData) -> int | None:
    """Minutes from the last reading to the poll (Logger's own clock, `as_of`)."""
    at = instant(dig(data.status, "glucose", "at"))
    if at is None:
        return None
    now = instant(data.status.get("as_of")) or dt_util.utcnow()
    return max(0, int((now - at).total_seconds() // 60))


class LoggerCoordinator(DataUpdateCoordinator[LoggerData]):
    """Polls one Logger; owns its client and its `/about`."""

    config_entry: LoggerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: LoggerConfigEntry,
        client: LoggerClient,
        about: About,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
            always_update=False,
        )
        self.client = client
        self.about = about
        self.alerts_on: bool = entry.options.get(CONF_ALERTS, DEFAULT_ALERTS)
        self._polls = 0
        self._quick_due = True

    def read_quick_next(self) -> None:
        """Read `/quick` again on the next poll."""
        self._quick_due = True

    async def _async_update_data(self) -> LoggerData:
        reread = self._quick_due or self.data is None or self._polls % QUICK_EVERY == 0
        try:
            status = await self.client.status()
            quick = await self.client.quick() if reread else self.data.quick
            alerts = await self.client.alerts() if self.alerts_on else None
        except LoggerError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"words": err.words},
            ) from err
        if reread:
            self._quick_due = False  # only now: a failed poll reads the list again
        self._polls += 1
        return LoggerData(status, quick, alerts)

    async def async_quick_now(self) -> list[dict[str, Any]]:
        """`/quick` read now (an action naming an entry the last read didn't have)."""
        quick = await self._ask(self.client.quick())
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, quick=quick))
        return quick

    async def async_fire(self, quick_id: int, **overrides: Any) -> dict[str, Any]:
        """Fire a quick entry → `{words, ids, kind, name, path}` (Logger's 201)."""
        try:
            answer = await self._ask(self.client.fire(quick_id, **overrides))
        except HomeAssistantError as err:
            if isinstance(err.__cause__, LoggerRefused) and err.__cause__.status == 404:
                self.read_quick_next()  # deleted in Logger: its button goes on the next poll
            raise
        self._fired(answer.get("kind"), answer.get("name"), answer.get("words"))
        await self.async_request_refresh()
        return answer

    async def async_shopping_add(self, name: str, **fields: Any) -> dict[str, Any]:
        """→ `{item, queued}`; queued = waiting for Nextcloud, still done."""
        answer = await self._ask(self.client.shopping_add(name, **fields))
        item = answer.get("item") if isinstance(answer.get("item"), dict) else {}
        queued = answer.get("queued") is True
        said = item.get("name") or name
        # Logger's own words for a shopping add (quick.shopping_words).
        words = (
            f"On the list once Nextcloud answers: {said}"
            if queued
            else f"On the list: {said}"
        )
        self._fired("shopping_add", said, words)
        await self.async_request_refresh()
        return {"item": item, "queued": queued}

    async def async_tablet_wake(self) -> dict[str, Any]:
        """→ `{outcome, words}`."""
        answer = await self._ask(self.client.tablet_wake())
        result = {"outcome": answer.get("outcome"), "words": answer.get("words")}
        self._fired("tablet_wake", None, result["words"])
        return result

    async def _ask[T](self, call: Awaitable[T]) -> T:
        """Logger's answer, or an HA error carrying Logger's sentence."""
        try:
            return await call
        except LoggerRefused as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refused",
                translation_placeholders={"words": err.words},
            ) from err
        except LoggerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"words": err.words},
            ) from err

    def _fired(self, kind: Any, name: Any, words: Any) -> None:
        self.hass.bus.async_fire(
            EVENT_FIRED, {"kind": kind, "name": name, "words": words}
        )
