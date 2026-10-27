"""Rules + scenes from Logger (F39): the cycle, the actions Logger queues, the HA-side
triggers of its HA → Logger rules, the webhook nudge — and an older Logger left alone."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
import voluptuous as vol

from custom_components.logger_app import triggers
from custom_components.logger_app.const import RETRY_REFUSED_EVERY
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    CoreState,
    Event,
    HassJob,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util

from .conftest import API, bodies, calls, mock_ha, mock_reads, setup_entry

# What Home Assistant has, as the contract's examples name it.
STATES: dict[str, tuple[str, dict[str, Any]]] = {
    "light.kitchen": (
        "off",
        {"friendly_name": "Kitchen", "supported_color_modes": ["color_temp"]},
    ),
    "media_player.kitchen_speaker": (
        "idle",
        {
            "friendly_name": "Kitchen speaker",
            "supported_features": 4 | 128 | 256 | 2048,  # volume, on, off, source
            "source_list": ["Radio Comercial", "Spotify"],
        },
    ),
    "tts.google_translate_pt_pt": ("unknown", {"friendly_name": "Google Translate pt"}),
    "scene.cinema": ("unknown", {"friendly_name": "Cinema"}),
    "script.good_night": ("off", {"friendly_name": "Good night"}),
    "switch.kettle": ("on", {"friendly_name": "Kettle"}),
    "assist_satellite.kitchen": ("idle", {"friendly_name": "Kitchen voice"}),
}
SERVICES = (
    "light.turn_on",
    "light.turn_off",
    "switch.turn_on",
    "switch.turn_off",
    "scene.turn_on",
    "script.turn_on",
    "tts.speak",
    "assist_satellite.announce",
    "notify.mobile_app_pixel_9",
    "media_player.turn_on",
    "media_player.turn_off",
    "media_player.volume_set",
    "media_player.select_source",
)


def ha_has(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    """The contract's entities as states, and every service an action calls (mocked)."""
    for entity_id, (state, attributes) in STATES.items():
        hass.states.async_set(entity_id, state, attributes)
    return {name: async_mock_service(hass, *name.split(".")) for name in SERVICES}


async def cycle(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.rules.async_cycle()
    await hass.async_block_till_done(wait_background_tasks=True)


def remock(
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    **ha: Any,
) -> None:
    """Logger's answers from now on (calls so far forgotten)."""
    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, **ha)


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> MockConfigEntry:
    """A Logger with rules, set up; its first cycle done."""
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry


def queue(contract: dict[str, Any], *actions: dict[str, Any]) -> dict[str, Any]:
    return {**contract["ha_actions"]["200"], "actions": list(actions)}


# ── The cycle ────────────────────────────────────────────────────────────────────


async def test_setup_runs_one_cycle(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """Started: the queue read, the rules and scenes read, the catalog sent."""
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(logger_api, "get", "/ha/actions")) == 1
    assert len(calls(logger_api, "get", "/ha/rules")) == 1
    assert len(calls(logger_api, "get", "/ha/scenes")) == 1
    [catalog] = bodies(logger_api, "put", "/ha/catalog")
    assert set(catalog) == set(contract["ha_catalog"]["request"])
    rules = config_entry.runtime_data.rules
    assert rules.last_error is None
    assert rules.rules_rev == contract["ha_rules"]["200"]["rev"]


async def test_each_poll_runs_a_cycle_and_revs_decide_the_reads(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Revs unchanged: the queue only. A rev moved: that list again."""
    remock(aioclient_mock, contract)
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/ha/actions")) == 1
    assert calls(aioclient_mock, "get", "/ha/rules") == []
    assert calls(aioclient_mock, "get", "/ha/scenes") == []
    assert calls(aioclient_mock, "put", "/ha/catalog") == []  # sent a minute ago

    remock(
        aioclient_mock,
        contract,
        actions={**queue(contract), "rules_rev": "5.1761560000"},
    )
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/ha/rules")) == 1
    assert calls(aioclient_mock, "get", "/ha/scenes") == []


async def test_a_failed_poll_runs_no_cycle(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/status", status=502, text="<html>Bad Gateway</html>")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(aioclient_mock, "get", "/ha/actions") == []


async def test_logger_silent_never_fails_the_poll(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """The cycle failing (Logger refusing the queue) is logged, the entities stay."""
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/ha/actions", status=500, json={"ok": False, "error": "boom"}
    )
    mock_reads(aioclient_mock, contract, ha=False)
    await cycle(hass, loaded)
    assert loaded.runtime_data.rules.last_error == "actions: boom"
    assert hass.states.get("sensor.logger_free_kcal").state == "75"


async def test_the_catalog_when_logger_wants_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """`catalog_wanted`, no catalog in Logger, a day gone by: sent again."""
    rules = loaded.runtime_data.rules
    for answer in ({"catalog_wanted": True}, {"catalog_at": None}):
        remock(aioclient_mock, contract, actions={**queue(contract), **answer})
        await rules.async_cycle()
        assert len(calls(aioclient_mock, "put", "/ha/catalog")) == 1, answer

    remock(aioclient_mock, contract)
    rules.catalog_at = dt_util.utcnow() - timedelta(hours=23)
    await rules.async_cycle()
    assert calls(aioclient_mock, "put", "/ha/catalog") == []
    rules.catalog_at -= timedelta(hours=1)
    await rules.async_cycle()
    assert len(calls(aioclient_mock, "put", "/ha/catalog")) == 1


async def test_catalog_follows_the_registries(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A registry change, 10 s quiet: sent — when it changed what Logger sees."""
    remock(aioclient_mock, contract)
    ar.async_get(hass).async_create("Garagem")  # no entity in it: nothing to send
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(aioclient_mock, "get", "/ha/actions")  # a cycle ran (a poll may too)
    assert calls(aioclient_mock, "put", "/ha/catalog") == []

    remock(aioclient_mock, contract)
    er.async_get(hass).async_get_or_create(
        "scene", "test", "night", suggested_object_id="night"
    )
    hass.states.async_set("scene.night", "unknown", {"friendly_name": "Night"})
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(aioclient_mock, "put", "/ha/catalog") == []  # still within the 10 s
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    [catalog] = bodies(aioclient_mock, "put", "/ha/catalog")
    assert {"entity_id": "scene.night", "name": "Night", "area": None} in (
        catalog["scenes"]
    )


async def test_logger_refuses_the_webhook(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`webhook: false`: one warning, and the poll carries everything."""
    await hass.config.async_update(internal_url="http://ha.example.org:8123")
    mock_reads(aioclient_mock, contract, ha=False)
    aioclient_mock.put(
        f"{API}/ha/catalog",
        json={
            **contract["ha_catalog"]["200"],
            "webhook": False,
            "webhook_error": "webhook must be a LAN address",
        },
    )
    mock_ha(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    [catalog] = bodies(aioclient_mock, "put", "/ha/catalog")
    assert catalog["ha"]["webhook"] == (
        f"http://ha.example.org:8123/api/webhook/{config_entry.data['webhook_id']}"
    )
    rules = config_entry.runtime_data.rules
    assert rules.webhook_accepted is False
    assert caplog.text.count("webhook must be a LAN address") == 1


async def test_catalog_refused_waits(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Logger says no to the catalog: not again every poll, but after a while."""
    mock_reads(aioclient_mock, contract, ha=False)
    aioclient_mock.put(
        f"{API}/ha/catalog", status=400, json={"ok": False, "error": "bad shape"}
    )
    mock_ha(aioclient_mock, contract, actions={**queue(contract), "catalog_at": None})
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "put", "/ha/catalog")) == 1
    assert config_entry.runtime_data.rules.last_error == "catalog: bad shape"
    await cycle(hass, config_entry)
    assert len(calls(aioclient_mock, "put", "/ha/catalog")) == 1
    freezer.tick(timedelta(minutes=6))
    await cycle(hass, config_entry)
    assert len(calls(aioclient_mock, "put", "/ha/catalog")) == 2


# ── Actions ──────────────────────────────────────────────────────────────────────


async def test_every_action_of_the_contract(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """Each kind runs the right service, in order, and its words go back to Logger."""
    service = ha_has(hass)
    remock(aioclient_mock, contract, actions=contract["ha_actions"]["200"])
    await cycle(hass, loaded)

    def data(name: str) -> list[dict[str, Any]]:
        return [dict(c.data) for c in service[name]]

    assert data("light.turn_on") == [
        {"entity_id": "light.kitchen", "flash": "long"},
        {
            "entity_id": "light.kitchen",
            "brightness_pct": 40,
            "color_temp_kelvin": 2700,
        },
    ]
    assert data("tts.speak") == [
        {
            "entity_id": "tts.google_translate_pt_pt",
            "media_player_entity_id": "media_player.kitchen_speaker",
            "message": "Omega-3 overdue since 08:00",
        }
    ]
    assert data("notify.mobile_app_pixel_9") == [
        {"message": "Glucose 62 mg/dL ↓", "title": "Glucose low"}
    ]
    assert data("scene.turn_on") == [{"entity_id": "scene.cinema"}]
    assert data("script.turn_on") == [{"entity_id": "script.good_night"}]
    assert data("switch.turn_off") == [{"entity_id": "switch.kettle"}]
    assert data("media_player.turn_on") == [
        {"entity_id": "media_player.kitchen_speaker"}
    ]
    assert data("media_player.volume_set") == [
        {"entity_id": "media_player.kitchen_speaker", "volume_level": 0.25}
    ]
    assert data("media_player.select_source") == [
        {"entity_id": "media_player.kitchen_speaker", "source": "Radio Comercial"}
    ]

    results = {
        action["id"]: bodies(
            aioclient_mock, "post", f"/ha/actions/{action['id']}/result"
        )
        for action in contract["ha_actions"]["200"]["actions"]
    }
    assert results == {
        41: [contract["ha_action_result"]["request_ok"]],  # Kitchen flashed
        42: [{"ok": True, "words": "Announced on Kitchen speaker"}],
        43: [{"ok": True, "words": "Sent to Pixel 9"}],
        44: [{"ok": True, "words": "Cinema on"}],
        45: [{"ok": True, "words": "Good night started"}],
        46: [{"ok": True, "words": "Kettle off"}],
        47: [{"ok": True, "words": "Evening: 2 set"}],
    }
    order = [
        c[1].path.split("/")[4]
        for c in aioclient_mock.mock_calls
        if c[1].path.endswith("/result")
    ]
    assert order == ["41", "42", "43", "44", "45", "46", "47"]


async def test_logger_scene_counts_what_ha_lacks(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    service = ha_has(hass)
    [action] = [a for a in contract["ha_actions"]["200"]["actions"] if a["id"] == 47]
    do = action["do"]
    entities = [
        *do["entities"],
        {**do["entities"][0], "entity_id": "light.gone"},
        {**do["entities"][0], "entity_id": "switch.kettle", "state": "off"},
    ]
    remock(
        aioclient_mock,
        contract,
        actions=queue(contract, {**action, "do": {**do, "entities": entities}}),
    )
    await cycle(hass, loaded)
    assert bodies(aioclient_mock, "post", "/ha/actions/47/result") == [
        {"ok": True, "words": "Evening: 3 set, 1 not in Home Assistant"}
    ]
    assert [dict(c.data) for c in service["switch.turn_off"]] == [
        {"entity_id": "switch.kettle"}
    ]


@pytest.mark.parametrize(
    ("do", "error"),
    [
        ({"type": "teleport"}, "Unknown action 'teleport'"),
        (
            {"type": "light", "entity_id": "light.gone", "state": "on"},
            "light.gone is not in Home Assistant",
        ),
        (
            {"type": "light", "entity_id": "switch.kettle", "state": "on"},
            "switch.kettle is not a light",
        ),
        (
            {"type": "switch", "entity_id": "switch.kettle", "state": "dim"},
            "Unknown state 'dim'",
        ),
        (
            {"type": "notify", "service": "notify.mobile_app_old", "title": None},
            "No notify.mobile_app_old in Home Assistant",
        ),
        (
            {"type": "scene", "entity_id": "scene.unavailable"},
            "scene.unavailable is unavailable",
        ),
    ],
)
async def test_an_action_that_fails(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
    do: dict[str, Any],
    error: str,
) -> None:
    ha_has(hass)
    hass.states.async_set("scene.unavailable", "unavailable")
    remock(aioclient_mock, contract, actions=queue(contract, {"id": 50, "do": do}))
    await cycle(hass, loaded)
    assert bodies(aioclient_mock, "post", "/ha/actions/50/result") == [
        {"ok": False, "error": error}
    ]


async def test_the_contracts_failed_light(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    ha_has(hass)
    hass.states.async_set("light.kitchen", "unavailable")
    [flash] = [a for a in contract["ha_actions"]["200"]["actions"] if a["id"] == 41]
    remock(aioclient_mock, contract, actions=queue(contract, flash))
    await cycle(hass, loaded)
    assert bodies(aioclient_mock, "post", "/ha/actions/41/result") == [
        contract["ha_action_result"]["request_failed"]
    ]


async def test_announce_without_tts_and_on_a_satellite(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    service = ha_has(hass)
    hass.states.async_remove("tts.google_translate_pt_pt")
    [announce] = [a for a in contract["ha_actions"]["200"]["actions"] if a["id"] == 42]
    to_satellite = {
        "id": 51,
        "do": {**announce["do"], "target": "assist_satellite.kitchen"},
    }
    remock(aioclient_mock, contract, actions=queue(contract, announce, to_satellite))
    await cycle(hass, loaded)
    assert bodies(aioclient_mock, "post", "/ha/actions/42/result") == [
        {"ok": False, "error": "No text-to-speech engine in Home Assistant"}
    ]
    assert bodies(aioclient_mock, "post", "/ha/actions/51/result") == [
        {"ok": True, "words": "Announced on Kitchen voice"}
    ]
    assert [dict(c.data) for c in service["assist_satellite.announce"]] == [
        {
            "entity_id": "assist_satellite.kitchen",
            "message": "Omega-3 overdue since 08:00",
        }
    ]


async def test_an_action_ha_refuses_or_that_hangs(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    ha_has(hass)

    async def refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("The kettle is not answering")

    async def hang(call: ServiceCall) -> None:
        await asyncio.sleep(10)

    hass.services.async_register("switch", "turn_off", refuse)
    hass.services.async_register("script", "turn_on", hang)
    actions = [
        a for a in contract["ha_actions"]["200"]["actions"] if a["id"] in (45, 46)
    ]
    remock(aioclient_mock, contract, actions=queue(contract, *actions))
    with patch("custom_components.logger_app.actions.ACTION_TIMEOUT", 0.05):
        await cycle(hass, loaded)
    assert bodies(aioclient_mock, "post", "/ha/actions/45/result") == [
        {"ok": False, "error": "Home Assistant didn't finish within 0.05 s"}
    ]
    assert bodies(aioclient_mock, "post", "/ha/actions/46/result") == [
        {"ok": False, "error": "The kettle is not answering"}
    ]


async def test_a_batch_of_actions_asks_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """Logger hands actions out in batches: after one, the queue is read again at once."""
    ha_has(hass)
    [flash] = [a for a in contract["ha_actions"]["200"]["actions"] if a["id"] == 41]
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{API}/ha/actions",
        side_effect=answers(
            {"json": queue(contract, flash)}, {"json": queue(contract)}
        ),
    )
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract)
    loaded.runtime_data.rules.async_request_cycle()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/ha/actions")) == 2
    assert len(calls(aioclient_mock, "post", "/ha/actions/41/result")) == 1


@pytest.mark.parametrize("status", [409, 404])
async def test_result_already_answered_or_gone_is_fine(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
    status: int,
) -> None:
    ha_has(hass)
    [flash] = [a for a in contract["ha_actions"]["200"]["actions"] if a["id"] == 41]
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{API}/ha/actions/41/result",
        status=status,
        json=contract["ha_action_result"]["409"]
        if status == 409
        else {"ok": False, "error": "No action #41"},
    )
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, actions=queue(contract, flash))
    await cycle(hass, loaded)
    assert loaded.runtime_data.rules.last_error is None


async def test_a_result_logger_missed_is_sent_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """The action ran; its result got no answer: sent at the next cycle, the action
    not run again (Logger hands each action out once)."""
    service = ha_has(hass)
    [flash] = [a for a in contract["ha_actions"]["200"]["actions"] if a["id"] == 41]
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{API}/ha/actions/41/result", exc=aiohttp.ClientConnectionError("refused")
    )
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, actions=queue(contract, flash))
    await cycle(hass, loaded)
    assert len(service["light.turn_on"]) == 1

    remock(aioclient_mock, contract)  # the queue is empty now
    await cycle(hass, loaded)
    assert bodies(aioclient_mock, "post", "/ha/actions/41/result") == [
        contract["ha_action_result"]["request_ok"]
    ]
    assert len(service["light.turn_on"]) == 1


# ── HA → Logger rules ────────────────────────────────────────────────────────────


def from_ha(contract: dict[str, Any], *changes: dict[str, Any]) -> dict[str, Any]:
    """The contract's from_ha rules, each changed by id."""
    by_id = {c["id"]: c for c in changes}
    return {
        **contract["ha_rules"]["200"],
        "rules": [
            {**r, **by_id.get(r["id"], {})}
            for r in contract["ha_rules"]["200"]["rules"]
        ],
    }


@pytest.fixture
async def ruled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> MockConfigEntry:
    """The contract's rules with the door rule enabled; the person, zone and door in HA."""
    hass.states.async_set("binary_sensor.front_door", "off", {"friendly_name": "Door"})
    hass.states.async_set(
        "zone.home",
        "0",
        {"latitude": 38.7, "longitude": -9.1, "radius": 100, "friendly_name": "Home"},
    )
    hass.states.async_set(
        "person.g",
        "not_home",
        {"latitude": 38.9, "longitude": -9.3, "gps_accuracy": 10, "friendly_name": "G"},
    )
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(
        aioclient_mock, contract, rules=from_ha(contract, {"id": 7, "enabled": True})
    )
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry


def uuid4_str(value: str) -> bool:
    return UUID(value).version == 4 and str(UUID(value)) == value


async def test_state_and_zone_rules_fire(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
) -> None:
    rules = ruled.runtime_data.rules
    assert rules.rules_registered == 2  # the zone and the door; the zha button refused
    # The door rule registers now: its old problem is cleared in Logger.
    assert bodies(aioclient_mock, "post", "/ha/rules/7/state") == [{"error": None}]

    hass.states.async_set("binary_sensor.front_door", "on", {"friendly_name": "Door"})
    await hass.async_block_till_done(wait_background_tasks=True)
    [fired] = bodies(aioclient_mock, "post", "/ha/rules/7/fire")
    assert set(fired) == set(contract["ha_rule_fire"]["request"])
    assert dt_util.parse_datetime(fired["at"]).utcoffset() is not None
    assert uuid4_str(fired["event"])

    hass.states.async_set(
        "person.g",
        "home",
        {"latitude": 38.7, "longitude": -9.1, "gps_accuracy": 10, "friendly_name": "G"},
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    [entered] = bodies(aioclient_mock, "post", "/ha/rules/4/fire")
    assert entered["event"] != fired["event"]
    assert rules.fired == 2

    # Leaving doesn't fire an `enter` rule; the door closing doesn't fire `to: on`.
    hass.states.async_set("person.g", "not_home", {"latitude": 38.9, "longitude": -9.3})
    hass.states.async_set("binary_sensor.front_door", "off")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(bodies(aioclient_mock, "post", "/ha/rules/4/fire")) == 1
    assert len(bodies(aioclient_mock, "post", "/ha/rules/7/fire")) == 1


async def test_a_device_rule_fires(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """The pill-box button: the device trigger as HA listed it (the catalog's, `trigger:`
    key) comes back in the rule, registers through HA's device automation, and a press
    POSTs `/fire`. zha itself stands in as a minimal device_trigger platform."""
    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=zha.entry_id,
        identifiers={("zha", "pill-box")},
        name="Pill box button",
    )

    class ZhaTriggers:
        TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
            {vol.Required("type"): str, vol.Required("subtype"): str}
        )

        @staticmethod
        async def async_attach_trigger(
            hass: HomeAssistant,
            config: dict[str, Any],
            action: Callable[..., Any],
            trigger_info: dict[str, Any],
        ) -> Callable[[], None]:
            job = HassJob(action)
            wanted = {k: config[k] for k in ("device_id", "type", "subtype")}

            @callback
            def pressed(event: Event) -> None:
                if event.data == wanted:
                    hass.async_run_hass_job(job, {"trigger": dict(wanted)})

            return hass.bus.async_listen("zha_event", pressed)

    async def platform(hass: HomeAssistant, domain: str, kind: Any) -> Any:
        return ZhaTriggers

    [button] = [r for r in contract["ha_rules"]["200"]["rules"] if r["id"] == 3]
    trigger = {**button["when"]["trigger"], "device_id": device.id}
    rules = {
        **contract["ha_rules"]["200"],
        "rules": [{**button, "when": {**button["when"], "trigger": trigger}}],
    }
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, rules=rules)
    with (
        patch(
            "homeassistant.components.device_automation.trigger"
            ".async_get_device_automation_platform",
            platform,
        ),
        patch(
            "homeassistant.components.device_automation.helpers"
            ".async_get_device_automation_platform",
            platform,
        ),
    ):
        await setup_entry(hass, config_entry)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert config_entry.runtime_data.rules.rules_registered == 1
        assert calls(aioclient_mock, "post", "/ha/rules/3/state") == []  # no problem
        hass.bus.async_fire(
            "zha_event",
            {"device_id": device.id, "type": trigger["type"], "subtype": "button_1"},
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    [fired] = bodies(aioclient_mock, "post", "/ha/rules/3/fire")
    assert uuid4_str(fired["event"])


async def test_a_trigger_ha_refuses_is_logger_s_problem(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    ruled: MockConfigEntry,
) -> None:
    """The contract's pill-box button: no such device here."""
    [state] = bodies(aioclient_mock, "post", "/ha/rules/3/state")
    assert state["error"].startswith("Home Assistant refused the trigger: ")
    assert ruled.runtime_data.rules.rules_refused == 1


async def test_a_device_trigger_logger_no_longer_has(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """Logger's catalog lost the trigger (`trigger: null`): the rule's problem says so."""
    [button] = [r for r in contract["ha_rules"]["200"]["rules"] if r["id"] == 3]
    rules = {
        **contract["ha_rules"]["200"],
        "rules": [{**button, "when": {**button["when"], "trigger": None}}],
    }
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, rules=rules)
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert bodies(aioclient_mock, "post", "/ha/rules/3/state") == [
        {"error": "Home Assistant no longer lists this device trigger"}
    ]


@pytest.mark.parametrize(
    "trigger",
    [
        {"trigger": "time_pattern", "seconds": "/1"},
        {"trigger": "webhook", "webhook_id": "open-door", "local_only": False},
        {"platform": "template", "value_template": "{{ true }}"},
        {"trigger": "device", "domain": "zha"},  # no device
        {"trigger": "device", "device_id": "c5a1e0b2"},  # no domain
        {"trigger": "device", "platform": "event", "domain": "zha", "device_id": "x"},
    ],
)
async def test_a_device_rule_takes_only_device_triggers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    trigger: dict[str, Any],
) -> None:
    """The config comes back from Logger: anything but a device trigger is refused before
    HA sees it, and is the rule's problem in Logger."""
    [button] = [r for r in contract["ha_rules"]["200"]["rules"] if r["id"] == 3]
    rules = {
        **contract["ha_rules"]["200"],
        "rules": [{**button, "when": {**button["when"], "trigger": trigger}}],
    }
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, rules=rules)
    with (
        patch.object(
            triggers,
            "async_validate_trigger_config",
            wraps=triggers.async_validate_trigger_config,
        ) as validate,
        patch.object(
            triggers,
            "async_initialize_triggers",
            wraps=triggers.async_initialize_triggers,
        ) as initialize,
    ):
        await setup_entry(hass, config_entry)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert bodies(aioclient_mock, "post", "/ha/rules/3/state") == [
        {"error": triggers.NOT_A_DEVICE}
    ]
    assert validate.call_count == initialize.call_count == 0
    assert config_entry.runtime_data.rules.rules_registered == 0
    assert "open-door" not in hass.data.get("webhook", {})


async def test_a_device_trigger_drops_templates(hass: HomeAssistant) -> None:
    """Options HA's device listings never carry, both templates: never taken."""
    config = triggers.trigger_config(
        {
            "type": "device",
            "trigger": {
                "trigger": "device",
                "domain": "zha",
                "device_id": "c5a1",
                "type": "remote_button_short_press",
                "variables": {"x": "{{ states('sensor.secret') }}"},
                "enabled": "{{ true }}",
            },
        }
    )
    assert config == {
        "trigger": "device",
        "domain": "zha",
        "device_id": "c5a1",
        "type": "remote_button_short_press",
    }
    with pytest.raises(triggers.TriggerRefused, match="Not a number of seconds"):
        triggers.trigger_config(
            {"type": "state", "entity_id": "lock.door", "to": "on", "for_s": "{{ 5 }}"}
        )


async def test_editing_one_rule_leaves_the_others_registered(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A new `rules_rev` touches only what changed: the door rule's pending `for:` runs on
    while the zone rule is edited; a rule turned off or deleted is removed."""
    hass.states.async_set("binary_sensor.front_door", "off")
    hass.states.async_set(
        "zone.home",
        "0",
        {"latitude": 38.7, "longitude": -9.1, "radius": 100, "friendly_name": "Home"},
    )
    hass.states.async_set("person.g", "not_home", {"latitude": 38.9, "longitude": -9.3})
    [_, zone_rule, door_rule] = contract["ha_rules"]["200"]["rules"]
    door = {
        **door_rule,
        "enabled": True,
        "problem": None,
        "when": {**door_rule["when"], "for_s": 10},
    }

    def listing(rev: str, *rules: dict[str, Any]) -> dict[str, Any]:
        return {"rev": rev, "rules": list(rules)}

    async def rules_now(listed: dict[str, Any]) -> None:
        remock(
            aioclient_mock,
            contract,
            actions={**queue(contract), "rules_rev": listed["rev"]},
            rules=listed,
        )
        await config_entry.runtime_data.rules.async_cycle()
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract, rules=listing("1", zone_rule, door))
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    rules = config_entry.runtime_data.rules
    door_registration = rules._registered[7]
    zone_registration = rules._registered[4]

    hass.states.async_set("binary_sensor.front_door", "on")  # 10 s from now: fires
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    left = {
        **zone_rule,
        "name": "Leaving home",
        "when": {**zone_rule["when"], "event": "leave"},
    }
    await rules_now(listing("2", left, door))
    assert rules._registered[7] is door_registration
    assert rules._registered[4] is not zone_registration
    assert calls(aioclient_mock, "post", "/ha/rules/7/fire") == []

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "post", "/ha/rules/7/fire")) == 1  # timer ran on

    renamed = {**left, "name": "Out"}  # a new name only: the same trigger
    zone_registration = rules._registered[4]
    await rules_now(listing("3", renamed, {**door, "enabled": False}))
    assert rules._registered[4] is zone_registration
    assert 7 not in rules._registered
    await rules_now(listing("4"))
    assert rules._registered == {}


async def test_a_problem_logger_knows_is_not_sent_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
) -> None:
    """Same outcome as Logger's `problem`: nothing posted; a disabled rule: nothing."""
    [state] = bodies(aioclient_mock, "post", "/ha/rules/3/state")
    remock(
        aioclient_mock,
        contract,
        actions={**queue(contract), "rules_rev": "5.1"},
        rules={
            **from_ha(contract, {"id": 3, "problem": state["error"]}),
            "rev": "5.1",
        },
    )
    await cycle(hass, ruled)
    assert len(calls(aioclient_mock, "get", "/ha/rules")) == 1
    assert calls(aioclient_mock, "post", "/ha/rules/3/state") == []
    assert calls(aioclient_mock, "post", "/ha/rules/7/state") == []
    # Rule 7 is disabled again: the door no longer fires anything.
    hass.states.async_set("binary_sensor.front_door", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(aioclient_mock, "post", "/ha/rules/7/fire") == []


async def test_a_refused_rule_is_tried_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
) -> None:
    """Every RETRY_REFUSED_EVERY cycles, the refused ones only (a device's integration
    may have been loading)."""
    rules = ruled.runtime_data.rules
    remock(aioclient_mock, contract)
    with patch.object(triggers, "async_attach", wraps=triggers.async_attach) as attach:
        while (rules._cycles + 1) % RETRY_REFUSED_EVERY:
            await cycle(hass, ruled)
        assert attach.call_count == 0
        await cycle(hass, ruled)
    assert [c.args[1]["id"] for c in attach.call_args_list] == [3]
    assert calls(aioclient_mock, "post", "/ha/rules/3/state") == []  # same problem


async def test_unload_removes_the_triggers_and_the_webhook(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    ruled: MockConfigEntry,
) -> None:
    webhook_id = ruled.data["webhook_id"]
    assert webhook_id in hass.data["webhook"]
    assert await hass.config_entries.async_unload(ruled.entry_id)
    assert ruled.state is ConfigEntryState.NOT_LOADED
    assert webhook_id not in hass.data["webhook"]
    hass.states.async_set("binary_sensor.front_door", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(aioclient_mock, "post", "/ha/rules/7/fire") == []


def answers(
    *steps: dict[str, Any],
) -> Callable[[str, Any, Any], Awaitable[Any]]:
    """A mock's side effect: each call the next answer (`exc` raised); the last stays."""
    left = list(steps)

    async def answer(method: str, url: Any, data: Any) -> Any:
        step = left.pop(0) if len(left) > 1 else left[0]
        if "exc" in step:
            raise step["exc"]
        return AiohttpClientMockResponse(method, url, **step)

    return answer


async def test_fire_retries_the_same_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
) -> None:
    """No answer, a proxy's 502: the same body again — Logger runs an `event` once."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(  # before mock_ha's catch-all: the first match answers
        f"{API}/ha/rules/7/fire",
        side_effect=answers(
            {"exc": aiohttp.ClientConnectionError("refused")},
            {"status": 502, "text": "<html>Bad Gateway</html>"},
            {"json": contract["ha_rule_fire"]["200"]},
        ),
    )
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract)
    with patch("custom_components.logger_app.rules.FIRE_RETRY_DELAYS", (0, 0, 0)):
        hass.states.async_set("binary_sensor.front_door", "on")
        await hass.async_block_till_done(wait_background_tasks=True)
    sent = bodies(aioclient_mock, "post", "/ha/rules/7/fire")
    assert len(sent) == 3
    assert sent[0] == sent[1] == sent[2]
    assert ruled.runtime_data.rules.fired == 1


async def test_fire_gives_up_after_the_retries(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/ha/rules/7/fire", exc=aiohttp.ClientConnectionError())
    with patch("custom_components.logger_app.rules.FIRE_RETRY_DELAYS", (0, 0, 0)):
        hass.states.async_set("binary_sensor.front_door", "on")
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "post", "/ha/rules/7/fire")) == 4
    assert "Logger rule 7 fired at" in caplog.text


@pytest.mark.parametrize("answer", ["200_skipped", "200_failed"])
async def test_fire_answers_are_loggers_business(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
    answer: str,
) -> None:
    """Quiet hours, a tick taken already: Logger's to show; not sent again."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/ha/rules/7/fire", json=contract["ha_rule_fire"][answer])
    hass.states.async_set("binary_sensor.front_door", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "post", "/ha/rules/7/fire")) == 1


async def test_fire_of_a_deleted_rule_reads_the_rules_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    ruled: MockConfigEntry,
) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(
        f"{API}/ha/rules/7/fire", status=404, json=contract["ha_rule_fire"]["404"]
    )
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract)  # rule 7 disabled
    hass.states.async_set("binary_sensor.front_door", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "post", "/ha/rules/7/fire")) == 1
    assert len(calls(aioclient_mock, "get", "/ha/rules")) == 1
    assert ruled.runtime_data.rules.rules_registered == 1  # the zone only


# ── The webhook ──────────────────────────────────────────────────────────────────


async def test_webhook_nudge_runs_a_cycle(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    client = await hass_client_no_auth()
    webhook = f"/api/webhook/{loaded.data['webhook_id']}"

    remock(aioclient_mock, contract)
    resp = await client.post(
        webhook, json={**contract["ha_webhook"], "logger": "other"}
    )
    assert resp.status == 200
    resp = await client.post(webhook, data="not json")
    assert resp.status == 200
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(aioclient_mock, "get", "/ha/actions") == []

    resp = await client.post(webhook, json=contract["ha_webhook"])
    assert resp.status == 200
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/ha/actions")) == 1
    assert calls(aioclient_mock, "put", "/ha/catalog") == []

    resp = await client.post(
        webhook, json={**contract["ha_webhook"], "reason": "catalog"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/ha/actions")) == 2
    assert len(calls(aioclient_mock, "put", "/ha/catalog")) == 1


async def test_nudges_during_a_cycle_run_one_more(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """Never two at once: asked thrice while one runs = one more after it."""
    rules = loaded.runtime_data.rules

    async def slow(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        await asyncio.sleep(0)  # Logger thinking: the cycle waits here
        return AiohttpClientMockResponse(method, url, json=queue(contract))

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/ha/actions", side_effect=slow)
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(aioclient_mock, contract)
    rules.async_request_cycle()
    rules.async_request_cycle()
    rules.async_request_cycle()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/ha/actions")) == 2


async def test_webhook_id_is_made_once(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Entries from 2026.10.0 have none: made at setup, kept across reloads."""
    await setup_entry(hass, config_entry)
    webhook_id = config_entry.data["webhook_id"]
    assert len(webhook_id) == 64
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.data["webhook_id"] == webhook_id
    assert webhook_id in hass.data["webhook"]


# ── An older Logger ──────────────────────────────────────────────────────────────


async def test_logger_without_rules_is_left_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No `features`: today's integration exactly — no /ha call, no webhook, no scene."""
    about = {k: v for k, v in contract["about"].items() if k != "features"}
    mock_reads(aioclient_mock, contract, about=about)
    await setup_entry(hass, config_entry)
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.rules is None
    assert [c for c in aioclient_mock.mock_calls if "/api/ha/" in str(c[1])] == []
    assert "webhook_id" not in config_entry.data
    assert not hass.data.get("webhook")
    assert hass.states.async_entity_ids("scene") == []


async def test_setup_waits_for_ha_to_start(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Set up while HA starts: nothing until it has started (services, devices there)."""
    hass.set_state(CoreState.starting)
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls(logger_api, "get", "/ha/actions") == []
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(logger_api, "get", "/ha/actions")) == 1
    assert len(calls(logger_api, "put", "/ha/catalog")) == 1
