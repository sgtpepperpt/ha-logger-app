"""Logger's scenes as `scene.logger_*` entities: they follow `scenes_rev`, and activating
one applies its states."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.scene import DOMAIN as SCENE_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import mock_ha, mock_reads, setup_entry

EVENING = "scene.logger_evening"


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> MockConfigEntry:
    hass.states.async_set("light.kitchen", "off", {"friendly_name": "Kitchen"})
    hass.states.async_set(
        "media_player.kitchen_speaker",
        "off",
        {"friendly_name": "Kitchen speaker", "supported_features": 4 | 128 | 2048},
    )
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry


async def activate(hass: HomeAssistant, entity_id: str, **data: Any) -> None:
    await hass.services.async_call(
        SCENE_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
    )


def services(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    return {
        name: async_mock_service(hass, *name.split("."))
        for name in (
            "light.turn_on",
            "light.turn_off",
            "media_player.turn_on",
            "media_player.volume_set",
            "media_player.select_source",
        )
    }


async def test_one_scene_per_logger_scene(
    hass: HomeAssistant, loaded: MockConfigEntry, contract: dict[str, Any]
) -> None:
    state = hass.states.get(EVENING)
    assert state is not None
    assert state.attributes["friendly_name"] == "Logger Evening"
    assert state.attributes["icon"] == "mdi:weather-sunset"
    assert state.attributes["id"] == 1
    assert state.attributes["entity_id"] == [
        "light.kitchen",
        "media_player.kitchen_speaker",
    ]
    assert er.async_get(hass).async_get(EVENING).unique_id == (
        f"{loaded.unique_id}_scene_1"
    )


async def test_activate_applies_the_states(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    called = services(hass)
    await activate(hass, EVENING, transition=2)
    assert [dict(c.data) for c in called["light.turn_on"]] == [
        {
            "entity_id": "light.kitchen",
            "brightness_pct": 40,
            "color_temp_kelvin": 2700,
            "transition": 2,
        }
    ]
    assert [dict(c.data) for c in called["media_player.turn_on"]] == [
        {"entity_id": "media_player.kitchen_speaker"}
    ]
    assert [dict(c.data) for c in called["media_player.volume_set"]] == [
        {"entity_id": "media_player.kitchen_speaker", "volume_level": 0.25}
    ]
    assert [dict(c.data) for c in called["media_player.select_source"]] == [
        {"entity_id": "media_player.kitchen_speaker", "source": "Radio Comercial"}
    ]
    assert hass.states.get(EVENING).state not in ("unknown", "unavailable")


async def test_activate_skips_what_ha_lacks_and_fails_on_a_refusal(
    hass: HomeAssistant, loaded: MockConfigEntry
) -> None:
    called = services(hass)
    hass.states.async_remove("media_player.kitchen_speaker")
    await activate(hass, EVENING)  # the light set, the speaker skipped: no error
    assert len(called["light.turn_on"]) == 1

    async def refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("Zigbee timeout")

    hass.services.async_register("light", "turn_on", refuse)
    with pytest.raises(
        HomeAssistantError,
        match=r"Evening: 0 set, 1 not in Home Assistant, 1 failed "
        r"\(light.kitchen: Zigbee timeout\)",
    ):
        await activate(hass, EVENING)


async def test_scenes_follow_logger(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    loaded: MockConfigEntry,
) -> None:
    """A new rev: a scene added appears, one deleted goes (from the registry too), one
    renamed keeps its entity id."""
    evening = contract["ha_scenes"]["200"]["scenes"][0]
    scenes = {
        "rev": "2.1761559999",
        "scenes": [
            {**evening, "name": "Evening calm", "icon": None},
            {**evening, "id": 2, "name": "Cinema night", "entities": []},
        ],
    }
    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(
        aioclient_mock,
        contract,
        actions={
            **contract["ha_actions"]["200"],
            "actions": [],
            "scenes_rev": "2.1761559999",
        },
        scenes=scenes,
    )
    await loaded.runtime_data.rules.async_cycle()
    await hass.async_block_till_done()
    renamed = hass.states.get(EVENING)
    assert renamed.attributes["friendly_name"] == "Logger Evening calm"
    assert "icon" not in renamed.attributes
    assert hass.states.get("scene.logger_cinema_night") is not None

    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract, ha=False)
    mock_ha(
        aioclient_mock,
        contract,
        actions={**contract["ha_actions"]["200"], "actions": [], "scenes_rev": "3.1"},
        scenes={"rev": "3.1", "scenes": scenes["scenes"][1:]},
    )
    await loaded.runtime_data.rules.async_cycle()
    await hass.async_block_till_done()
    assert hass.states.get(EVENING) is None
    assert er.async_get(hass).async_get(EVENING) is None
    assert hass.states.get("scene.logger_cinema_night") is not None


async def test_scene_deleted_while_ha_was_off(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        SCENE_DOMAIN,
        "logger_app",
        f"{config_entry.unique_id}_scene_99",
        config_entry=config_entry,
        suggested_object_id="logger_gone",
    )
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert er.async_get(hass).async_get("scene.logger_gone") is None
    assert hass.states.get(EVENING) is not None


async def test_no_scenes_without_the_feature(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """An older Logger: none — and those left from a Logger that had them, removed."""
    config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        SCENE_DOMAIN,
        "logger_app",
        f"{config_entry.unique_id}_scene_1",
        config_entry=config_entry,
        suggested_object_id="logger_evening",
    )
    about = {k: v for k, v in contract["about"].items() if k != "features"}
    mock_reads(aioclient_mock, contract, about=about)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids(SCENE_DOMAIN) == []
    assert er.async_get(hass).async_get(EVENING) is None
