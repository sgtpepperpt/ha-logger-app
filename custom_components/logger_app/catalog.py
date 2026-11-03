"""The list of Home Assistant entities and devices sent to the server."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import logging
from typing import Any

from homeassistant.components.device_automation import (
    DeviceAutomationType,
    async_get_device_automations,
)
from homeassistant.components.light import (
    brightness_supported,
    color_supported,
    color_temp_supported,
    get_supported_color_modes,
)
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.components.webhook import async_generate_path
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    CONF_ENTITY_ID,
    __version__ as HA_VERSION,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CAP = 500
ENTITIES_CAP = 1000
DEVICE_TRIGGERS_CAP = 2000

TRIGGER_DOMAINS = (
    "binary_sensor",
    "input_boolean",
    "input_select",
    "switch",
    "light",
    "lock",
    "cover",
    "person",
    "device_tracker",
    "alarm_control_panel",
    "media_player",
)
FIXED_STATES = {
    "binary_sensor": ["on", "off"],
    "input_boolean": ["on", "off"],
    "switch": ["on", "off"],
    "light": ["on", "off"],
    "lock": ["locked", "unlocked"],
    "cover": ["open", "closed"],
}
NOT_A_TARGET = {"send_message", "persistent_notification"}

type Row = dict[str, Any]


def webhook_url(hass: HomeAssistant, webhook_id: str) -> str | None:
    try:
        base = get_url(
            hass, allow_external=False, allow_cloud=False, prefer_external=False
        )
    except NoURLAvailableError:
        return None
    return f"{base}{async_generate_path(webhook_id)}"


def digest(catalog: dict[str, Any]) -> str:
    return hashlib.sha1(
        json.dumps(catalog, sort_keys=True, default=str).encode(), usedforsecurity=False
    ).hexdigest()


async def async_build(hass: HomeAssistant, webhook: str | None) -> dict[str, Any]:
    lookup = _Lookup(hass)
    zones = lookup.rows("zone", with_area=False)
    away = [z["name"] for z in zones if z[CONF_ENTITY_ID] != "zone.home"]
    return {
        "ha": {"version": HA_VERSION, "webhook": webhook},
        "scenes": lookup.rows("scene"),
        "scripts": lookup.rows("script"),
        "lights": lookup.rows("light", extra=lambda state, _: _light(hass, state)),
        "switches": lookup.rows("switch"),
        "media_players": lookup.rows("media_player", extra=_media_player),
        "tts": lookup.rows("tts", with_area=False),
        "satellites": lookup.rows("assist_satellite"),
        "notify": notify_targets(hass),
        "persons": lookup.rows("person", with_area=False),
        "zones": zones,
        "entities": lookup.rows(
            TRIGGER_DOMAINS, extra=_trigger_states(away), cap=ENTITIES_CAP
        ),
        "device_triggers": await _device_triggers(hass, lookup),
    }


def notify_targets(hass: HomeAssistant) -> list[Row]:
    phones = _phone_names(hass)
    rows = [
        {"service": f"notify.{service}", "name": phones.get(service) or _human(service)}
        for service in hass.services.async_services_for_domain("notify")
        if service not in NOT_A_TARGET
    ]
    return sorted(rows, key=lambda r: (r["name"].casefold(), r["service"]))[:CAP]


def notify_name(hass: HomeAssistant, service: str) -> str:
    name = service.removeprefix("notify.")
    return _phone_names(hass).get(name) or _human(name)


def _phone_names(hass: HomeAssistant) -> dict[str, str]:
    names: dict[str, str] = {}
    for entry in hass.config_entries.async_entries("mobile_app"):
        if isinstance(name := entry.data.get("device_name"), str) and name:
            names[slugify(f"mobile_app_{name}")] = name
    return names


def _human(name: str) -> str:
    words = name.removeprefix("mobile_app_").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


class _Lookup:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.entities = er.async_get(hass)
        self.devices = dr.async_get(hass)
        self.areas = ar.async_get(hass)

    def area(self, area_id: str | None) -> str | None:
        area = self.areas.async_get_area(area_id) if area_id else None
        return area.name if area else None

    def entity_area(self, entry: er.RegistryEntry | None) -> str | None:
        if entry is None:
            return None
        if entry.area_id:
            return self.area(entry.area_id)
        device = self.devices.async_get(entry.device_id) if entry.device_id else None
        return self.area(device.area_id) if device else None

    def rows(
        self,
        domains: str | tuple[str, ...],
        *,
        with_area: bool = True,
        extra: Callable[[State, str], Row] | None = None,
        cap: int = CAP,
    ) -> list[Row]:
        rows = []
        for state in self.hass.states.async_all(domains):
            entry = self.entities.async_get(state.entity_id)
            if entry is not None and (
                entry.hidden_by or entry.disabled_by or entry.platform == DOMAIN
            ):
                continue
            row: Row = {CONF_ENTITY_ID: state.entity_id, "name": state.name}
            if with_area:
                row["area"] = self.entity_area(entry)
            if extra is not None:
                row.update(extra(state, state.domain))
            rows.append(row)
        rows.sort(key=lambda r: (r["name"].casefold(), r[CONF_ENTITY_ID]))
        if len(rows) > cap:
            _LOGGER.debug("Catalog: %s capped at %s of %s", domains, cap, len(rows))
        return rows[:cap]


def _light(hass: HomeAssistant, state: State) -> Row:
    modes = get_supported_color_modes(hass, state.entity_id)
    return {
        "brightness": brightness_supported(modes),
        "color_temp": color_temp_supported(modes),
        "color": color_supported(modes),
    }


def _media_player(state: State, _domain: str) -> Row:
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
    sources = state.attributes.get("source_list")
    return {
        "volume": isinstance(features, int)
        and bool(features & MediaPlayerEntityFeature.VOLUME_SET),
        "sources": [str(s) for s in sources] if isinstance(sources, list) else [],
    }


def _trigger_states(zones: list[str]) -> Callable[[State, str], Row]:
    where = ["home", "not_home", *zones]

    def states(state: State, domain: str) -> Row:
        hint: list[str] | None = FIXED_STATES.get(domain)
        if domain in ("person", "device_tracker"):
            hint = where
        elif domain == "input_select":
            options = state.attributes.get("options")
            hint = [str(o) for o in options] if isinstance(options, list) else None
        return {"domain": domain, "states": hint}

    return states


async def _device_triggers(hass: HomeAssistant, lookup: _Lookup) -> list[Row]:
    found = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER)
    rows = []
    for device_id, triggers in found.items():
        device = lookup.devices.async_get(device_id)
        if device is None or device.disabled_by:
            continue
        name = device.name_by_user or device.name or device_id
        for trigger in triggers:
            if CONF_ENTITY_ID in trigger:
                continue
            config = _config(trigger)
            rows.append(
                {
                    "id": trigger_id(config),
                    "device": name,
                    "area": lookup.area(device.area_id),
                    "words": _trigger_words(config),
                    "trigger": config,
                }
            )
    rows.sort(key=lambda r: (r["device"].casefold(), r["words"], r["id"]))
    return rows[:DEVICE_TRIGGERS_CAP]


TRIGGER_ID_KEYS = ("domain", "device_id", "type", "subtype", "discovery_id")


def trigger_id(config: dict[str, Any]) -> str:
    ident = "|".join(str(config.get(key) or "") for key in TRIGGER_ID_KEYS)
    return hashlib.sha1(ident.encode(), usedforsecurity=False).hexdigest()[:16]


def _config(trigger: dict[str, Any]) -> dict[str, Any]:
    config = {k: v for k, v in trigger.items() if k not in ("platform", "metadata")}
    config = {"trigger": trigger.get("platform", "device"), **config}
    return json.loads(json.dumps(config, default=str))


def _trigger_words(config: dict[str, Any]) -> str:
    parts = [
        _human(str(config[key])) if n == 0 else str(config[key]).replace("_", " ")
        for n, key in enumerate(k for k in ("subtype", "type") if config.get(k))
    ]
    return " · ".join(parts) or "trigger"
