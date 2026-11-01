"""The calendar: Logger's events as one calendar entity, `calendar.logger_calendar` (F74).

Only with a Logger whose `/about.features` has "calendar". Its state and `event` (on while an event
is on: the one under way, else the next) come from the poll's `/status.calendar` — today's
occurrences (`rows`, all-day first) and the next one still to start (`next`) — with no read of its
own; the calendar panel and `calendar.get_events` read `GET /calendar/events?from&to&tz` (Logger's
range read: the occurrences overlapping the window, each event once in `events`, by its id), at most
CALENDAR_CHUNK_DAYS a call. Logger's null (`calendar` null: Calendar off or not connected) is
unavailable. Read-only: Logger's Calendar tab writes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import LoggerError
from .const import CALENDAR_CHUNK_DAYS, CALENDAR_MAX_CHUNKS, DOMAIN, FEATURE_CALENDAR
from .coordinator import LoggerConfigEntry, LoggerCoordinator, instant
from .entity import LoggerEntity

PARALLEL_UPDATES = 0  # the range reads are the calendar panel's, the state the poll's

UNTITLED = "Untitled event"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if FEATURE_CALENDAR in coordinator.about.features:
        async_add_entities([LoggerCalendar(coordinator)])


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _summary(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else UNTITLED


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def glance_event(row: Any) -> CalendarEvent | None:
    """A `/status.calendar` row (or its `next`) as HA's event: an all-day one by its days (`date`
    … `last_date`, HA's end exclusive), a timed one by its instants; None when unreadable."""
    if not isinstance(row, dict):
        return None
    summary, location = _summary(row.get("summary")), _text(row.get("where"))
    if row.get("all_day") is True:
        first = _day(row.get("date"))
        if first is None:
            return None
        last = _day(row.get("last_date")) or first
        return CalendarEvent(
            start=first,
            end=max(last, first) + timedelta(days=1),
            summary=summary,
            location=location,
        )
    start = instant(row.get("start"))
    if start is None:
        return None
    end = instant(row.get("end")) or start
    return CalendarEvent(
        start=start, end=max(end, start), summary=summary, location=location
    )


def range_event(inst: Any, events: Any) -> CalendarEvent | None:
    """An occurrence of `GET /calendar/events` as HA's event: its own words (an override's), its
    event's notes and uid, its RECURRENCE-ID; None when unreadable."""
    if not isinstance(inst, dict):
        return None
    event = events.get(str(inst.get("event_id"))) if isinstance(events, dict) else None
    event = event if isinstance(event, dict) else {}
    words = {
        "summary": _summary(inst.get("summary")),
        "location": _text(inst.get("location")),
        "description": _text(event.get("description")),
        "uid": _text(event.get("uid")),
        "recurrence_id": _text(inst.get("recurrence_id")),
    }
    if inst.get("all_day") is True:
        first = _day(inst.get("start_date"))
        if first is None:
            return None
        after = _day(inst.get("end_date"))
        return CalendarEvent(
            start=first,
            end=after
            if after is not None and after > first
            else first + timedelta(days=1),
            **words,
        )
    start = instant(inst.get("start"))
    if start is None:
        return None
    end = instant(inst.get("end")) or start
    return CalendarEvent(start=start, end=max(end, start), **words)


class LoggerCalendar(LoggerEntity, CalendarEntity):
    """`calendar.logger_calendar`: every calendar Logger mirrors (its picked ones), as one."""

    _attr_translation_key = "calendar"

    def __init__(self, coordinator: LoggerCoordinator) -> None:
        super().__init__(coordinator, "calendar")

    def _glance(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        glance = data.status.get("calendar") if data is not None else None
        return glance if isinstance(glance, dict) else None

    @property
    def available(self) -> bool:
        return super().available and self._glance() is not None

    @property
    def event(self) -> CalendarEvent | None:
        """The occurrence under way (today's rows), else the next one still to start."""
        glance = self._glance()
        if glance is None:
            return None
        now = dt_util.now()
        rows = glance.get("rows") if isinstance(glance.get("rows"), list) else []
        for row in rows:
            current = glance_event(row)
            if (
                current is not None
                and current.start_datetime_local <= now < current.end_datetime_local
            ):
                return current
        return glance_event(glance.get("next"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        glance = self._glance()
        today = glance.get("today_n") if glance is not None else None
        return (
            {"today": today}
            if isinstance(today, int) and not isinstance(today, bool)
            else None
        )

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """[start_date, end_date) as Logger reads it — in calls of at most a year (its cap is
        400 days), the first CALENDAR_MAX_CHUNKS of them."""
        out: list[CalendarEvent] = []
        lo = start_date
        for _ in range(CALENDAR_MAX_CHUNKS):
            if lo >= end_date:
                break
            hi = min(end_date, lo + timedelta(days=CALENDAR_CHUNK_DAYS))
            try:
                answer = await self.coordinator.client.calendar_events(lo, hi)
            except LoggerError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="cannot_connect",
                    translation_placeholders={"words": err.words},
                ) from err
            events = answer.get("events")
            instances = answer.get("instances")
            for inst in instances if isinstance(instances, list) else []:
                found = range_event(inst, events)
                # One crossing a chunk's edge is in both reads: kept once.
                if found is not None and not (
                    lo > start_date and found.start_datetime_local < lo
                ):
                    out.append(found)
            lo = hi
        return out
