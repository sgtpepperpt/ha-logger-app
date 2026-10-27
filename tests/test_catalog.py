"""The catalog of what Home Assistant has (`PUT /ha/catalog`): the contract's shape, built
from a Home Assistant holding the contract's examples."""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.logger_app import catalog
from custom_components.logger_app.const import DOMAIN
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

WEBHOOK = "http://192.168.1.20:8123/api/webhook/logger_app_5c0d2e7f4a1b"


def shape(value: Any) -> dict[str, set[str]]:
    """Every key path (`lists[].key`) → the JSON types found there."""
    found: dict[str, set[str]] = {}

    def walk(value: Any, path: str) -> None:
        kind = (
            "null"
            if value is None
            else "bool"
            if isinstance(value, bool)
            else "number"
            if isinstance(value, int | float)
            else "str"
            if isinstance(value, str)
            else "list"
            if isinstance(value, list)
            else "object"
        )
        found.setdefault(path, set()).add(kind)
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for item in value:
                walk(item, f"{path}[]")

    walk(value, "")
    return found


@pytest.fixture
async def ha(hass: HomeAssistant) -> dict[str, Any]:
    """Home Assistant with the contract's examples (+ what must stay out)."""
    areas = ar.async_get(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    cozinha = areas.async_create("Cozinha")
    sala = areas.async_create("Sala")
    entrada = areas.async_create("Entrada")

    zha = MockConfigEntry(domain="zha")
    zha.add_to_hass(hass)
    button = devices.async_get_or_create(
        config_entry_id=zha.entry_id,
        identifiers={("zha", "pill-box")},
        name="TS0041",
    )
    devices.async_update_device(
        button.id, area_id=cozinha.id, name_by_user="Pill box button"
    )
    kitchen_hub = devices.async_get_or_create(
        config_entry_id=zha.entry_id, identifiers={("zha", "hub")}, name="Hub"
    )
    devices.async_update_device(kitchen_hub.id, area_id=cozinha.id)
    phone = MockConfigEntry(
        domain="mobile_app", data={"device_name": "Pixel 9"}, title="Pixel 9"
    )
    phone.add_to_hass(hass)

    def entity(
        entity_id: str,
        state: str,
        attributes: dict[str, Any],
        **registry: Any,
    ) -> None:
        if registry:
            domain, object_id = entity_id.split(".")
            entry = entities.async_get_or_create(
                domain,
                registry.pop("platform", "test"),
                object_id,
                suggested_object_id=object_id,
            )
            entities.async_update_entity(entry.entity_id, **registry)
        hass.states.async_set(entity_id, state, attributes)

    entity("scene.cinema", "unknown", {"friendly_name": "Cinema"}, area_id=sala.id)
    entity("script.good_night", "off", {"friendly_name": "Good night"})
    entity(
        "light.kitchen",
        "off",
        {"friendly_name": "Kitchen", "supported_color_modes": ["color_temp"]},
        device_id=kitchen_hub.id,  # its area: the device's
    )
    entity("switch.kettle", "on", {"friendly_name": "Kettle"}, area_id=cozinha.id)
    entity(
        "media_player.kitchen_speaker",
        "idle",
        {
            "friendly_name": "Kitchen speaker",
            "supported_features": 4 | 2048,
            "source_list": ["Radio Comercial", "Spotify"],
        },
        area_id=cozinha.id,
    )
    entity(
        "tts.google_translate_pt_pt",
        "unknown",
        {"friendly_name": "Google Translate pt"},
    )
    entity(
        "assist_satellite.kitchen",
        "idle",
        {"friendly_name": "Kitchen voice"},
        area_id=cozinha.id,
    )
    entity("person.g", "home", {"friendly_name": "G"})
    entity("zone.home", "1", {"friendly_name": "Home"})
    entity(
        "binary_sensor.front_door",
        "off",
        {"friendly_name": "Front door"},
        area_id=entrada.id,
    )
    # Left out: hidden, disabled, and this integration's own.
    entity(
        "light.hidden",
        "off",
        {"friendly_name": "Hidden"},
        hidden_by=er.RegistryEntryHider.USER,
    )
    entity(
        "switch.disabled",
        "off",
        {"friendly_name": "Disabled"},
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    entity(
        "scene.logger_evening",
        "unknown",
        {"friendly_name": "Logger Evening"},
        platform=DOMAIN,
    )
    for service in ("mobile_app_pixel_9", "send_message", "persistent_notification"):
        async_mock_service(hass, "notify", service)

    triggers = {
        button.id: [
            {
                "platform": "device",
                "domain": "zha",
                "device_id": button.id,
                "type": "remote_button_short_press",
                "subtype": "button_1",
            },
            {  # names an entity: an entity-state trigger, left out
                "platform": "device",
                "domain": "light",
                "device_id": button.id,
                "entity_id": "light.kitchen",
                "type": "turned_on",
                "metadata": {"secondary": False},
            },
        ],
        kitchen_hub.id: [],
    }
    return {"button": button, "triggers": triggers}


async def build(hass: HomeAssistant, ha: dict[str, Any]) -> dict[str, Any]:
    with patch.object(
        catalog, "async_get_device_automations", return_value=ha["triggers"]
    ):
        return await catalog.async_build(hass, WEBHOOK)


async def test_the_contracts_shape(
    hass: HomeAssistant, ha: dict[str, Any], contract: dict[str, Any]
) -> None:
    """Every key path of `ha_catalog.request`, nothing else, the same JSON types (null
    where the contract has a value: unknown, e.g. no area)."""
    ours = shape(await build(hass, ha))
    theirs = shape(contract["ha_catalog"]["request"])
    assert set(ours) == set(theirs)
    for path, kinds in theirs.items():
        if kinds != {"null"}:
            assert ours[path] <= kinds | {"null"}, path


async def test_the_contracts_rows(
    hass: HomeAssistant, ha: dict[str, Any], contract: dict[str, Any]
) -> None:
    built = await build(hass, ha)
    request = contract["ha_catalog"]["request"]
    assert built["ha"] == {"version": HA_VERSION, "webhook": WEBHOOK}
    for key in (
        "scenes",
        "scripts",
        "lights",
        "switches",
        "media_players",
        "tts",
        "satellites",
        "notify",
        "persons",
        "zones",
    ):
        assert built[key] == request[key], key
    [door] = [e for e in built["entities"] if e["domain"] == "binary_sensor"]
    assert door == request["entities"][0]

    [trigger] = built["device_triggers"]
    config = {**request["device_triggers"][0]["trigger"], "device_id": ha["button"].id}
    ident = f"zha|{ha['button'].id}|remote_button_short_press|button_1|"
    assert trigger == {
        "id": hashlib.sha1(ident.encode(), usedforsecurity=False).hexdigest()[:16],
        "device": "Pill box button",
        "area": "Cozinha",
        "words": "Button 1 · remote button short press",
        "trigger": config,
    }


async def test_state_hints(hass: HomeAssistant, ha: dict[str, Any]) -> None:
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set(
        "input_select.mode",
        "Day",
        {"friendly_name": "Mode", "options": ["Day", "Night"]},
    )
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front lock"})
    hass.states.async_set("media_player.tv", "off", {"friendly_name": "TV"})
    states = {e["entity_id"]: e["states"] for e in (await build(hass, ha))["entities"]}
    assert states["person.g"] == ["home", "not_home", "Work"]
    assert states["input_select.mode"] == ["Day", "Night"]
    assert states["lock.front"] == ["locked", "unlocked"]
    assert states["light.kitchen"] == ["on", "off"]
    assert states["media_player.tv"] is None  # type one


async def test_lists_are_capped(hass: HomeAssistant) -> None:
    for n in range(catalog.CAP + 5):
        hass.states.async_set(
            f"scene.s{n:03}", "unknown", {"friendly_name": f"S{n:03}"}
        )
    with patch.object(catalog, "async_get_device_automations", return_value={}):
        built = await catalog.async_build(hass, None)
    assert len(built["scenes"]) == catalog.CAP
    assert built["scenes"][0]["entity_id"] == "scene.s000"  # by name


async def test_webhook_url(hass: HomeAssistant) -> None:
    """HA's internal address only — never the external or cloud one."""
    await hass.config.async_update(
        internal_url="http://192.168.1.20:8123",
        external_url="https://ha.example.org",
    )
    assert (
        catalog.webhook_url(hass, "abc") == "http://192.168.1.20:8123/api/webhook/abc"
    )
    with patch.object(
        catalog, "get_url", side_effect=catalog.NoURLAvailableError
    ) as get_url:
        assert catalog.webhook_url(hass, "abc") is None
    assert get_url.call_args.kwargs == {
        "allow_external": False,
        "allow_cloud": False,
        "prefer_external": False,
    }


async def test_notify_names(hass: HomeAssistant) -> None:
    """A phone's own name; another target's service name, humanized."""
    MockConfigEntry(
        domain="mobile_app", data={"device_name": "Pixel 9 Pro"}, title="x"
    ).add_to_hass(hass)
    assert catalog.notify_name(hass, "notify.mobile_app_pixel_9_pro") == "Pixel 9 Pro"
    assert catalog.notify_name(hass, "notify.mobile_app_old_phone") == "Old phone"
    assert catalog.notify_name(hass, "notify.telegram_family") == "Telegram family"
