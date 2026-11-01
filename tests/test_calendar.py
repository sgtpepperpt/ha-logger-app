"""The calendar entity (F74): its state and event from `/status.calendar` (the one under way, else
the next), its days from `GET /calendar/events` (in calls of at most a year), only with a Logger
whose `/about.features` has "calendar", unavailable while Logger's Calendar is off."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.calendar import glance_event, range_event
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import API, calls, mock_reads, setup_entry

ENTITY = "calendar.logger_calendar"


def _status(contract: dict[str, Any], calendar: Any) -> dict[str, Any]:
    return {**contract["status"], "calendar": calendar}


def _timed_only(contract: dict[str, Any]) -> dict[str, Any]:
    """The contract's calendar (Lisbon's 5 Nov, the dentist next at 16:00) without its all-day rows."""
    glance = contract["status"]["calendar"]
    return {**glance, "rows": [r for r in glance["rows"] if not r["all_day"]]}


async def test_the_next_event_while_none_is_on(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-11-05T12:00:00+00:00")
    mock_reads(
        aioclient_mock, contract, status=_status(contract, _timed_only(contract))
    )
    await setup_entry(hass, config_entry)
    state = hass.states.get(ENTITY)
    assert state.state == STATE_OFF
    assert state.attributes["message"] == "Dentist"
    assert state.attributes["location"] == "Clinic, Rua Nova 1"
    assert state.attributes["all_day"] is False
    assert state.attributes["today"] == 5


async def test_on_while_an_event_is_under_way(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-11-05T13:45:00+00:00")
    mock_reads(
        aioclient_mock, contract, status=_status(contract, _timed_only(contract))
    )
    await setup_entry(hass, config_entry)
    state = hass.states.get(ENTITY)
    assert state.state == STATE_ON
    assert state.attributes["message"] == "Lunch with Sam"


async def test_an_all_day_one_is_on_all_day(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    # Home Assistant's own rule: an all-day event is on all its day; the first under way is the one
    # shown — today's rows come all-day first (the conference that runs all week).
    freezer.move_to("2026-11-05T14:00:00+00:00")
    await setup_entry(hass, config_entry)
    state = hass.states.get(ENTITY)
    assert state.state == STATE_ON
    assert state.attributes["message"] == "Conference"
    assert state.attributes["all_day"] is True


async def test_unavailable_while_calendar_is_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract, status=_status(contract, None))
    await setup_entry(hass, config_entry)
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


async def test_no_calendar_without_the_feature(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(
        aioclient_mock, contract, about={**contract["about"], "features": ["rules"]}
    )
    await setup_entry(hass, config_entry)
    assert hass.states.get(ENTITY) is None


async def test_get_events_reads_logger_s_range(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    logger_api.get(f"{API}/calendar/events", json=contract["calendar_events"])
    await setup_entry(hass, config_entry)
    start = datetime(2026, 11, 5, tzinfo=dt_util.UTC)
    answer = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": ENTITY,
            "start_date_time": start,
            "end_date_time": start + timedelta(days=1),
        },
        blocking=True,
        return_response=True,
    )
    events = answer[ENTITY]["events"]
    assert [e["summary"] for e in events] == ["Holiday", "Standup", "Dentist"]
    assert (events[0]["start"], events[0]["end"]) == ("2026-11-05", "2026-11-06")
    assert events[2]["description"] == "Bring the referral"
    assert events[2]["location"] == "Clinic, Rua Nova 1"
    [call] = calls(logger_api, "GET", "/calendar/events")
    params = dict(call[1].query)
    assert params["tz"] == hass.config.time_zone
    assert datetime.fromisoformat(params["from"]) == start
    assert datetime.fromisoformat(params["to"]) == start + timedelta(days=1)


async def test_a_long_window_goes_in_years(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    logger_api.get(f"{API}/calendar/events", json={"instances": [], "events": {}})
    await setup_entry(hass, config_entry)
    start = datetime(2026, 1, 1, tzinfo=dt_util.UTC)
    await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": ENTITY,
            "start_date_time": start,
            "end_date_time": start + timedelta(days=800),
        },
        blocking=True,
        return_response=True,
    )
    windows = [dict(c[1].query) for c in calls(logger_api, "GET", "/calendar/events")]
    spans = [
        datetime.fromisoformat(w["to"]) - datetime.fromisoformat(w["from"])
        for w in windows
    ]
    assert spans == [timedelta(days=365), timedelta(days=365), timedelta(days=70)]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {
                "summary": " ",
                "start": "2026-11-05T16:00:00+00:00",
                "end": "2026-11-05T17:00:00+00:00",
            },
            "Untitled event",
        ),
        (
            {
                "summary": "Trip",
                "all_day": True,
                "date": "2026-11-04",
                "last_date": "2026-11-06",
            },
            "Trip",
        ),
        ({"summary": "No start"}, None),
        ("not an event", None),
    ],
)
def test_a_glance_row(row: Any, expected: str | None) -> None:
    event = glance_event(row)
    assert (event.summary if event else None) == expected
    if expected == "Trip":
        assert (event.start, event.end) == (date(2026, 11, 4), date(2026, 11, 7))


def test_an_override_s_words_and_its_event_s_notes() -> None:
    inst = {
        "event_id": 7,
        "recurrence_id": "2026-11-05T09:00:00Z",
        "start": "2026-11-05T09:00:00+00:00",
        "end": "2026-11-05T09:30:00+00:00",
        "all_day": False,
        "summary": "Standup (moved)",
        "location": None,
    }
    event = range_event(inst, {"7": {"uid": "standup-1", "description": "Daily"}})
    assert (event.summary, event.uid, event.recurrence_id, event.description) == (
        "Standup (moved)",
        "standup-1",
        "2026-11-05T09:00:00Z",
        "Daily",
    )
    assert range_event({"event_id": 1, "all_day": True, "start_date": None}, {}) is None
