"""The actions: `logger_app.fire`, `logger_app.shopping_add`, `logger_app.tablet_wake`.

Registered once (`async_setup`), not per entry. Which Logger: `config_entry_id`, or the only
one loaded. Each answers with Logger's words (response optional) and, like a button press,
fires `logger_app_fired {kind, name, words}`; a refusal is an HA error with Logger's sentence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, ATTR_NAME
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    KCAL_MAX,
    SERVICE_FIRE,
    SERVICE_SHOPPING_ADD,
    SERVICE_TABLET_WAKE,
)
from .coordinator import LoggerCoordinator

ATTR_ENTRY = "entry"
ATTR_TEXT = "text"
ATTR_NOTE = "note"
ATTR_KCAL = "kcal"
ATTR_AT = "at"
ATTR_QTY = "qty"
ATTR_SHOP = "shop"
ATTR_DUE = "due"

FIRE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_ENTRY): cv.string,
        vol.Optional(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_NOTE): cv.string,
        vol.Optional(ATTR_KCAL): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=KCAL_MAX)
        ),
        vol.Optional(ATTR_AT): cv.datetime,
    }
)
SHOPPING_ADD_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_QTY): cv.string,
        vol.Optional(ATTR_SHOP): cv.string,
        vol.Optional(ATTR_DUE): cv.date,
    }
)
TABLET_WAKE_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    async def fire(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        quick_id = await _quick_id(coordinator, call.data[ATTR_ENTRY])
        kcal = call.data.get(ATTR_KCAL)
        at = call.data.get(ATTR_AT)
        answer = await coordinator.async_fire(
            quick_id,
            text=call.data.get(ATTR_TEXT),
            note=call.data.get(ATTR_NOTE),
            kcal=int(kcal) if kcal is not None and kcal.is_integer() else kcal,
            at=await _instant(at, coordinator.client.tz) if at is not None else None,
        )
        return {k: answer.get(k) for k in ("words", "ids", "kind", "name")}

    async def shopping_add(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return await coordinator.async_shopping_add(
            call.data[ATTR_NAME],
            qty=call.data.get(ATTR_QTY),
            shop=call.data.get(ATTR_SHOP),
            due=call.data.get(ATTR_DUE),
        )

    async def tablet_wake(call: ServiceCall) -> ServiceResponse:
        return await _coordinator(hass, call).async_tablet_wake()

    for name, handler, schema in (
        (SERVICE_FIRE, fire, FIRE_SCHEMA),
        (SERVICE_SHOPPING_ADD, shopping_add, SHOPPING_ADD_SCHEMA),
        (SERVICE_TABLET_WAKE, tablet_wake, TABLET_WAKE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> LoggerCoordinator:
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
                translation_placeholders={"entry_id": entry_id},
            )
        return entry.runtime_data
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if len(entries) == 1:
        return entries[0].runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="which_logger" if entries else "no_logger",
    )


async def _quick_id(coordinator: LoggerCoordinator, wanted: str) -> int:
    """`entry` = a quick entry's id, or its exact name (read again from Logger once when
    the last read of the list doesn't have it)."""
    wanted = wanted.strip()
    if wanted.isascii() and wanted.isdigit():
        return int(wanted)
    quick: list[dict[str, Any]] = coordinator.data.quick
    for fresh in (False, True):
        if fresh:
            quick = await coordinator.async_quick_now()
        ids = [e["id"] for e in quick if e.get("name") == wanted]
        if len(ids) > 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="ambiguous_quick_entry",
                translation_placeholders={"entry": wanted},
            )
        if ids:
            return ids[0]
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_quick_entry",
        translation_placeholders={"entry": wanted},
    )


async def _instant(at: datetime, tz: str) -> str:
    """An instant Logger takes (`…+00:00`); a time without a zone is on the entry's clock."""
    if at.tzinfo is None:
        zone = await dt_util.async_get_time_zone(tz) or dt_util.get_default_time_zone()
        at = at.replace(tzinfo=zone)
    return at.astimezone(UTC).isoformat()
