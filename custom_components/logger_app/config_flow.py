"""Add a Logger by its LAN address; the entry is keyed by Logger's `instance_id`.

`user`: the address → `GET /api/about` → unique_id = instance_id (an address is not an
identity: the same Logger moved, or restored from a backup, stays one entry). `reconfigure`:
a new address for the same Logger. Options (reload on save): scan interval, time zone, alerts,
voice sentences (F69: write Assist's sentences from Logger's words; off by default).
"""

from __future__ import annotations

import logging
from typing import Any
import zoneinfo

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_URL
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import About, LoggerClient, LoggerError, normalize_url
from .const import (
    API_MAX,
    CONF_ALERTS,
    CONF_TZ,
    CONF_VOICE,
    DEFAULT_ALERTS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_URL,
    DEFAULT_VOICE,
    DOMAIN,
    LAN_EXAMPLE,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NAME,
)
from .coordinator import LoggerConfigEntry

_LOGGER = logging.getLogger(__name__)

URL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        )
    }
)
PLACEHOLDERS = {"lan": LAN_EXAMPLE}


class LoggerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: LoggerConfigEntry) -> LoggerOptionsFlow:
        return LoggerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url, about, errors = await self._ask(user_input[CONF_URL])
            if about is not None:
                await self.async_set_unique_id(about.instance_id)
                self._abort_if_unique_id_configured(updates={CONF_URL: url})
                return self.async_create_entry(title=NAME, data={CONF_URL: url})
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                URL_SCHEMA, user_input or {CONF_URL: DEFAULT_URL}
            ),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url, about, errors = await self._ask(user_input[CONF_URL])
            if about is not None:
                await self.async_set_unique_id(about.instance_id)
                self._abort_if_unique_id_mismatch(reason="wrong_logger")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: url}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                URL_SCHEMA, user_input or entry.data
            ),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def _ask(self, raw: str) -> tuple[str, About | None, dict[str, str]]:
        """(the normalized address, its `/about` or None, the form's errors)."""
        try:
            url = normalize_url(raw)
        except ValueError:
            return raw, None, {CONF_URL: "invalid_url"}
        client = LoggerClient(
            async_get_clientsession(self.hass), url, self.hass.config.time_zone
        )
        try:
            about = await client.about()
        except LoggerError as err:
            _LOGGER.debug("No Logger at %s: %s", url, err.words)
            return url, None, {"base": "cannot_connect"}
        if about.api > API_MAX:
            raise AbortFlow(
                "unsupported_api",
                description_placeholders={"api": str(about.api), "max": str(API_MAX)},
            )
        return url, about, {}


class LoggerOptionsFlow(OptionsFlowWithReload):
    """Scan interval, the time zone that decides Logger's "today", the alerts sensor, the voice
    sentences."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if await dt_util.async_get_time_zone(user_input[CONF_TZ]) is None:
                errors[CONF_TZ] = "invalid_tz"
            else:
                return self.async_create_entry(
                    data={
                        **user_input,
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    }
                )
        zones = sorted(
            await self.hass.async_add_executor_job(zoneinfo.available_timezones)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_TZ): SelectSelector(
                    SelectSelectorConfig(
                        options=zones,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_ALERTS): BooleanSelector(),
                vol.Required(CONF_VOICE): BooleanSelector(),
            }
        )
        shown = {
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_TZ: self.hass.config.time_zone,
            CONF_ALERTS: DEFAULT_ALERTS,
            CONF_VOICE: DEFAULT_VOICE,
            **self.config_entry.options,
            **(user_input or {}),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, shown),
            errors=errors,
        )
