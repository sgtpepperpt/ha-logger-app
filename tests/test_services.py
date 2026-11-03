"""Tests for the services."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.const import (
    DOMAIN,
    EVENT_FIRED,
    SERVICE_FIRE,
    SERVICE_SHOPPING_ADD,
    SERVICE_TABLET_WAKE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import API, calls, mock_reads, setup_entry


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> AiohttpClientMocker:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(
        f"{API}/quick/12/fire", status=201, json=contract["fire"]["201"]
    )
    aioclient_mock.post(
        f"{API}/quick/13/fire", status=201, json=contract["fire"]["201"]
    )
    await setup_entry(hass, config_entry)
    return aioclient_mock


async def call(
    hass: HomeAssistant, service: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )


async def test_fire_by_name(hass: HomeAssistant, loaded: AiohttpClientMocker) -> None:
    events = async_capture_events(hass, EVENT_FIRED)
    answer = await call(
        hass, SERVICE_FIRE, {"entry": "Coffee", "note": "with a friend"}
    )
    assert answer == {
        "words": "Diary: Coffee",
        "ids": [812],
        "kind": "diary",
        "name": "Coffee",
    }
    [(_, _, body, _)] = calls(loaded, "post", "/quick/12/fire")
    assert json.loads(body) == {"note": "with a friend", "tz": hass.config.time_zone}
    assert events[0].data["words"] == "Diary: Coffee"


async def test_fire_by_id_with_overrides(
    hass: HomeAssistant, loaded: AiohttpClientMocker
) -> None:
    await call(
        hass,
        SERVICE_FIRE,
        {"entry": 13, "text": "Lunch out", "kcal": 700, "at": "2026-10-25 12:30:00"},
    )
    [(_, _, body, _)] = calls(loaded, "post", "/quick/13/fire")
    sent = json.loads(body)
    assert sent["text"] == "Lunch out"
    assert sent["kcal"] == 700
    assert isinstance(sent["kcal"], int)
    local = datetime(2026, 10, 25, 12, 30, tzinfo=ZoneInfo(hass.config.time_zone))
    assert sent["at"] == local.astimezone(UTC).isoformat()


async def test_fire_unknown_name_rereads_the_list(
    hass: HomeAssistant, loaded: AiohttpClientMocker
) -> None:
    with pytest.raises(ServiceValidationError, match="Nope"):
        await call(hass, SERVICE_FIRE, {"entry": "Nope"})
    assert len(calls(loaded, "get", "/quick")) == 2


async def test_fire_ambiguous_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    twin = {**contract["quick"][0], "id": 99}
    mock_reads(aioclient_mock, contract, quick=[*contract["quick"], twin])
    await setup_entry(hass, config_entry)
    with pytest.raises(ServiceValidationError, match="give its id"):
        await call(hass, SERVICE_FIRE, {"entry": "Coffee"})


async def test_fire_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(
        f"{API}/quick/20/fire",
        status=400,
        json={
            "ok": False,
            "error": "at does not apply to a med tick: it answers the dose owed now, taken now",
        },
    )
    await setup_entry(hass, config_entry)
    with pytest.raises(HomeAssistantError, match="does not apply to a med tick"):
        await call(
            hass, SERVICE_FIRE, {"entry": "20", "at": "2026-10-25T08:00:00+00:00"}
        )


async def test_shopping_add_queued(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(f"{API}/shopping", status=202, json=contract["shopping"]["202"])
    await setup_entry(hass, config_entry)
    events = async_capture_events(hass, EVENT_FIRED)

    answer = await call(
        hass, SERVICE_SHOPPING_ADD, {"name": "Bread", "due": "2026-10-27"}
    )
    assert answer["queued"] is True
    assert answer["item"]["name"] == "Bread"
    [(_, _, body, headers)] = calls(aioclient_mock, "post", "/shopping")
    assert json.loads(body) == {
        "name": "Bread",
        "due": {"date": "2026-10-27", "time": None},
        "tz": hass.config.time_zone,
    }
    assert headers["X-Logger-Client"] == "ha"
    assert events[0].data == {
        "kind": "shopping_add",
        "name": "Bread",
        "words": "On the list once synced: Bread",
    }


async def test_shopping_add_done(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(f"{API}/shopping", status=201, json=contract["shopping"]["201"])
    await setup_entry(hass, config_entry)
    answer = await call(
        hass,
        SERVICE_SHOPPING_ADD,
        {"name": "Milk", "qty": "2 l", "shop": "Market"},
    )
    assert answer["queued"] is False
    [(_, _, body, _)] = calls(aioclient_mock, "post", "/shopping")
    assert json.loads(body) == {
        "name": "Milk",
        "qty": "2 l",
        "shop": "Market",
        "tz": hass.config.time_zone,
    }


async def test_tablet_wake(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    aioclient_mock.post(f"{API}/tablet/wake", json=contract["tablet_wake"]["200"])
    await setup_entry(hass, config_entry)
    assert await call(hass, SERVICE_TABLET_WAKE, {}) == {
        "outcome": "woken_push",
        "words": "Woken. One swipe opens it.",
    }


async def test_no_logger_loaded(
    hass: HomeAssistant, loaded: AiohttpClientMocker
) -> None:
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError, match="No Logger"):
        await call(hass, SERVICE_TABLET_WAKE, {})
    with pytest.raises(ServiceValidationError, match="No loaded Logger entry"):
        await call(hass, SERVICE_TABLET_WAKE, {"config_entry_id": entry.entry_id})
