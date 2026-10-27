"""The webhook Logger nudges: `POST /api/webhook/<id>` with `{logger: <instance_id>, reason}`.

Registered only with the rules feature on. `local_only`: Home Assistant ignores it from
outside the LAN. No auth, by Home Assistant's design: the random id in the URL is the only
secret, so the body carries a nudge, never data — any nudge naming this Logger runs one cycle
now (`reason: catalog` also sends the catalog) and everything of substance is fetched from
Logger. Logger unable to reach HA changes nothing but the delay: the poll carries it all.

The id is made once and kept in the entry (`webhook_id`; entries from 2026.10.0 have none).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp.hdrs import METH_POST
from aiohttp.web import Request

from homeassistant.components import webhook
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, NAME
from .coordinator import LoggerConfigEntry

if TYPE_CHECKING:
    from .rules import LoggerRules

_LOGGER = logging.getLogger(__name__)


@callback
def async_webhook_id(hass: HomeAssistant, entry: LoggerConfigEntry) -> str:
    """The entry's webhook id, made and kept on first need."""
    if isinstance(webhook_id := entry.data.get(CONF_WEBHOOK_ID), str) and webhook_id:
        return webhook_id
    webhook_id = webhook.async_generate_id()
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_WEBHOOK_ID: webhook_id}
    )
    return webhook_id


@callback
def async_register(
    hass: HomeAssistant, entry: LoggerConfigEntry, rules: LoggerRules
) -> None:
    """Listen for Logger's nudges until the entry unloads."""

    async def handle(hass: HomeAssistant, webhook_id: str, request: Request) -> None:
        try:
            body = await request.json()
        except ValueError:
            _LOGGER.debug("Logger webhook: not JSON, ignored")
            return
        if not isinstance(body, dict) or body.get("logger") != rules.instance_id:
            _LOGGER.debug("Logger webhook: not from this Logger, ignored")
            return
        rules.async_request_cycle(catalog=body.get("reason") == "catalog")

    webhook.async_register(
        hass,
        DOMAIN,
        NAME,
        rules.webhook_id,
        handle,
        local_only=True,
        allowed_methods=[METH_POST],
    )
    rules.webhook_registered = True

    @callback
    def unregister() -> None:
        webhook.async_unregister(hass, rules.webhook_id)
        rules.webhook_registered = False

    entry.async_on_unload(unregister)
