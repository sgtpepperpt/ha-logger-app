"""Sensors and binary sensors read the contract's `/status`; Logger's null is unknown, never 0."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import mock_reads, setup_entry


@pytest.mark.parametrize(
    ("entity_id", "state"),
    [
        ("sensor.logger_free_kcal", "75"),
        ("sensor.logger_eaten_kcal", "1161"),
        ("sensor.logger_planned_kcal", "864"),
        ("sensor.logger_goal_kcal", "2100"),
        ("sensor.logger_banked_kcal", "372"),
        ("sensor.logger_glucose", "94"),
        ("sensor.logger_medication_overdue", "1"),
        ("sensor.logger_medication_due", "1"),
        ("sensor.logger_medication_next", "2026-10-25T21:00:00+00:00"),
        ("sensor.logger_care_overdue", "0"),
        ("sensor.logger_care_due", "1"),
        ("sensor.logger_steps_today", "6234"),
        ("sensor.logger_tasks_overdue", "1"),
        ("sensor.logger_tasks_today", "1"),
        ("sensor.logger_tasks_waiting", "2"),
        ("sensor.logger_deliveries_overdue", "0"),
        ("sensor.logger_deliveries_today", "1"),
        ("sensor.logger_problems", "2"),
        ("sensor.logger_refill_soon", "1"),
        ("sensor.logger_sensor_day", "3"),
        ("sensor.logger_sensor_ends", "2026-11-07T13:47:12+00:00"),
        ("binary_sensor.logger_medication_overdue", STATE_ON),
        ("binary_sensor.logger_tasks_overdue", STATE_ON),
        ("binary_sensor.logger_glucose_low", STATE_OFF),
        ("binary_sensor.logger_glucose_stale", STATE_OFF),
        ("binary_sensor.logger_tasks_read_only", STATE_OFF),
        ("binary_sensor.logger_sensor_ending_soon", STATE_OFF),
    ],
)
async def test_contract_status(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    entity_id: str,
    state: str,
) -> None:
    await setup_entry(hass, config_entry)
    assert hass.states.get(entity_id).state == state


async def test_unique_ids(
    hass: HomeAssistant, logger_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    entry = er.async_get(hass).async_get("sensor.logger_free_kcal")
    assert entry.unique_id == f"{config_entry.unique_id}_free_kcal"


async def test_attributes(
    hass: HomeAssistant, logger_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)

    glucose = hass.states.get("sensor.logger_glucose").attributes
    assert glucose["unit_of_measurement"] == "mg/dL"
    assert glucose["direction"] == "FortyFiveDown"
    assert glucose["source"] == "nightscout"
    assert (glucose["low"], glucose["high"]) == (70, 180)
    assert (
        glucose["minutes_ago"] == 3
    )  # 13:27 reading, polled at 13:30 (Logger's as_of)

    overdue = hass.states.get("sensor.logger_medication_overdue").attributes
    assert overdue["rows"] == [
        {
            "name": "Omega-3",
            "words": "1 tab",
            "due": "2026-10-25T08:00:00+00:00",
            "period": "Morning",
        }
    ]
    assert hass.states.get("sensor.logger_medication_next").attributes["name"] == (
        "Magnesium"
    )
    tasks = hass.states.get("sensor.logger_tasks_overdue").attributes["rows"]
    assert [r["summary"] for r in tasks] == ["Call the clinic"]
    today = hass.states.get("sensor.logger_tasks_today").attributes["rows"]
    assert [r["summary"] for r in today] == ["Water the plants"]
    deliveries = hass.states.get("sensor.logger_deliveries_today").attributes["rows"]
    assert [r["summary"] for r in deliveries] == ["Shoes"]
    assert hass.states.get("sensor.logger_refill_soon").attributes["rows"][0] == {
        "name": "Omega-3",
        "days_left": 6,
        "refill_by": "2026-10-29",
        "approx": False,
    }
    cgm = hass.states.get("sensor.logger_sensor_day").attributes
    assert (cgm["of"], cgm["via"]) == (15, "detected")
    problems = hass.states.get("sensor.logger_problems").attributes["texts"]
    assert len(problems) == 2


async def test_null_is_unknown_never_zero(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """No calorie goal, nothing scheduled, Tasks off, no CGM, no stock counted."""
    status = contract["status"]
    status["food"] |= {"free": None, "banked": None}
    status |= {"glucose": None, "medication": None, "care": None}
    status |= {"steps": None, "tasks": None}
    del status["refill"]
    mock_reads(aioclient_mock, contract, status=status)
    await setup_entry(hass, config_entry)

    for entity_id in (
        "sensor.logger_free_kcal",
        "sensor.logger_banked_kcal",
        "sensor.logger_glucose",
        "sensor.logger_medication_overdue",
        "sensor.logger_medication_due",
        "sensor.logger_medication_next",
        "sensor.logger_care_due",
        "sensor.logger_steps_today",
        "sensor.logger_tasks_overdue",
        "sensor.logger_tasks_waiting",
        "sensor.logger_deliveries_today",
        "sensor.logger_refill_soon",
        "sensor.logger_sensor_day",
        "sensor.logger_sensor_ends",
        "binary_sensor.logger_medication_overdue",
        "binary_sensor.logger_tasks_overdue",
        "binary_sensor.logger_glucose_low",
        "binary_sensor.logger_glucose_stale",
        "binary_sensor.logger_tasks_read_only",
        "binary_sensor.logger_sensor_ending_soon",
    ):
        assert hass.states.get(entity_id).state == STATE_UNKNOWN, entity_id
    assert hass.states.get("sensor.logger_eaten_kcal").state == "1161"
    # No rows Logger didn't send: an unknown count carries no empty list either.
    for entity_id in ("sensor.logger_medication_overdue", "sensor.logger_refill_soon"):
        assert "rows" not in hass.states.get(entity_id).attributes


async def test_glucose_low(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    contract["status"]["glucose"]["mgdl"] = 62
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    assert hass.states.get("binary_sensor.logger_glucose_low").state == STATE_ON


@pytest.mark.parametrize(
    ("age", "glucose", "stale", "low"),
    [
        (timedelta(minutes=10), "62", STATE_OFF, STATE_ON),
        (timedelta(minutes=11), "62", STATE_ON, STATE_UNKNOWN),
        (timedelta(hours=23), "62", STATE_ON, STATE_UNKNOWN),
        (timedelta(hours=25), STATE_UNKNOWN, STATE_ON, STATE_UNKNOWN),
    ],
)
async def test_glucose_age(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    age: timedelta,
    glucose: str,
    stale: str,
    low: str,
) -> None:
    """Logger's widget rules: stale past 10 min (a low then says nothing), hidden past a
    day."""
    as_of = dt_util.parse_datetime(contract["status"]["as_of"])
    contract["status"]["glucose"] |= {"mgdl": 62, "at": (as_of - age).isoformat()}
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    assert hass.states.get("sensor.logger_glucose").state == glucose
    assert hass.states.get("binary_sensor.logger_glucose_stale").state == stale
    assert hass.states.get("binary_sensor.logger_glucose_low").state == low


async def test_alerts_option_off(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """No `/alerts` read, no `problems` sensor — and a former one is removed."""
    await setup_entry(hass, config_entry)
    assert hass.states.get("sensor.logger_problems") is not None

    hass.config_entries.async_update_entry(config_entry, options={"alerts": False})
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get("sensor.logger_problems") is None
    assert hass.states.get("sensor.logger_problems") is None
