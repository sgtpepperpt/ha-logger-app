"""Quick-entry buttons (follow `/quick`), the tablet wake, refresh."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.const import EVENT_FIRED, QUICK_EVERY
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import API, calls, mock_reads, setup_entry

QUICK_BUTTONS = {
    "button.logger_cafe": (12, "diary", "mdi:coffee"),
    "button.logger_felt_a_drop": (31, "symptom", "mdi:water"),
    "button.logger_lunch_out": (13, "placeholder", "mdi:silverware-fork-knife"),
    "button.logger_morning_box": (20, "intake", "mdi:pill"),
    "button.logger_home": (16, "task_nudge", "mdi:home"),
    "button.logger_shopping": (21, "shopping_add", "mdi:cart"),
}


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_one_button_per_quick_entry(
    hass: HomeAssistant, logger_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    for entity_id, (quick_id, kind, icon) in QUICK_BUTTONS.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.attributes["id"] == quick_id
        assert state.attributes["kind"] == kind
        assert state.attributes["icon"] == icon
        assert registry.async_get(entity_id).unique_id == (
            f"{config_entry.unique_id}_quick_{quick_id}"
        )
    assert hass.states.get("button.logger_tablet_wake") is not None
    assert hass.states.get("button.logger_refresh") is not None


async def test_press_fires_with_tz(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(
        f"{API}/quick/12/fire", status=201, json=contract["fire"]["201"]
    )
    await setup_entry(hass, config_entry)
    events = async_capture_events(hass, EVENT_FIRED)

    await press(hass, "button.logger_cafe")

    [(_, _, body, headers)] = calls(aioclient_mock, "post", "/quick/12/fire")
    assert json.loads(body) == {"tz": hass.config.time_zone}
    assert headers["X-Logger-Client"] == "ha"
    assert headers["Content-Type"] == "application/json"
    assert "Origin" not in headers
    assert [e.data for e in events] == [
        {"kind": "diary", "name": "Café", "words": "Diary: Café"}
    ]


async def test_press_refused_carries_loggers_words(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """A tick already taken: Logger's 409 sentence, not a connection error."""
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(
        f"{API}/quick/20/fire", status=409, json=contract["fire"]["409"]
    )
    await setup_entry(hass, config_entry)
    events = async_capture_events(hass, EVENT_FIRED)

    with pytest.raises(HomeAssistantError, match="already taken: Omega-3, Zinc"):
        await press(hass, "button.logger_morning_box")
    assert events == []
    assert hass.states.get("button.logger_morning_box").state != STATE_UNAVAILABLE


async def test_press_unreachable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(f"{API}/quick/12/fire", exc=TimeoutError())
    await setup_entry(hass, config_entry)
    with pytest.raises(HomeAssistantError, match="couldn't be reached"):
        await press(hass, "button.logger_cafe")


async def test_quick_list_follows_logger(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Read on the first poll and every QUICK_EVERY-th: an entry deleted in Logger loses its
    button (gone from the registry), a new one gets one."""
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    assert len(calls(aioclient_mock, "get", "/quick")) == 1

    quick = [e for e in contract["quick"] if e["id"] != 12]
    quick.append({**contract["quick"][0], "id": 40, "name": "Tea", "icon": None})
    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract, quick=quick)

    for _ in range(QUICK_EVERY - 1):
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert calls(aioclient_mock, "get", "/quick") == []
    assert hass.states.get("button.logger_cafe") is not None

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "get", "/quick")) == 1
    assert hass.states.get("button.logger_cafe") is None
    assert er.async_get(hass).async_get("button.logger_cafe") is None
    tea = hass.states.get("button.logger_tea")
    assert tea is not None
    assert tea.attributes["icon"] == "mdi:notebook-edit"  # no glyph picked: the kind's


async def test_quick_entry_renamed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    quick = [dict(e) for e in contract["quick"]]
    quick[0]["name"] = "Coffee"
    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract, quick=quick)

    await press(hass, "button.logger_refresh")
    await hass.async_block_till_done()
    state = hass.states.get("button.logger_cafe")  # the entity_id stays
    assert state.attributes["friendly_name"] == "Logger Coffee"


async def test_deleted_while_ha_was_off(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        BUTTON_DOMAIN,
        "logger_app",
        f"{config_entry.unique_id}_quick_99",
        config_entry=config_entry,
        suggested_object_id="logger_gone",
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get("button.logger_gone") is None


async def test_press_of_a_deleted_entry_rereads_the_list(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Logger's 404: the list is read again on the very next poll, not the 10th."""
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(
        f"{API}/quick/12/fire",
        status=404,
        json={"ok": False, "error": "no quick entry #12"},
    )
    await setup_entry(hass, config_entry)
    with pytest.raises(HomeAssistantError, match="no quick entry #12"):
        await press(hass, "button.logger_cafe")

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "get", "/quick")) == 2


async def test_tablet_wake(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(f"{API}/tablet/wake", json=contract["tablet_wake"]["200"])
    await setup_entry(hass, config_entry)
    events = async_capture_events(hass, EVENT_FIRED)
    await press(hass, "button.logger_tablet_wake")
    assert events[0].data == {
        "kind": "tablet_wake",
        "name": None,
        "words": "Woken. One swipe opens it.",
    }


async def test_tablet_wake_not_set_up(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(
        f"{API}/tablet/wake", status=409, json=contract["tablet_wake"]["409"]
    )
    await setup_entry(hass, config_entry)
    with pytest.raises(HomeAssistantError, match="No tablet gets Logger"):
        await press(hass, "button.logger_tablet_wake")


async def test_poll_failure_makes_entities_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", status=502, text="<html>Bad Gateway</html>")

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.logger_free_kcal").state == STATE_UNAVAILABLE
    assert hass.states.get("button.logger_cafe").state == STATE_UNAVAILABLE
    assert hass.states.get("button.logger_refresh").state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract)
    await press(hass, "button.logger_refresh")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.logger_free_kcal").state == "75"


async def test_failed_poll_reads_the_list_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """`/quick` read but `/alerts` failed: the poll failed, so the next one reads it again."""
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/about", json=contract["about"])
    aioclient_mock.get(f"{API}/status", json=contract["status"])
    aioclient_mock.get(f"{API}/quick", json=contract["quick"])
    aioclient_mock.get(f"{API}/alerts", status=502, text="<html>Bad Gateway</html>")
    await press(hass, "button.logger_refresh")
    assert hass.states.get("sensor.logger_free_kcal").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract)
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls(aioclient_mock, "get", "/quick")) == 1
    assert hass.states.get("sensor.logger_free_kcal").state == "75"
