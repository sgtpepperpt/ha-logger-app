"""Logger — the Home Assistant client of the self-hosted health logger *Logger*.

One config entry per Logger (keyed by its `instance_id`), polled on its LAN address. Entities
read `/status`; buttons and actions write through Logger's own routes, named `ha` in its Audit
tab. The contract with Logger = tests/fixtures/ha_contract.json + `api` (const.API_MAX).
A Logger whose `/about.features` has "rules" also gets its hands here (rules.py): the actions
it queues, its HA-side triggers, its scenes, the catalog of what HA has, a webhook it nudges.
One whose features have "calendar" gets its calendar entity (calendar.py); "voice", Assist's
commands and answers and — with the voice option on — the sentences file (voice.py).
"""

from __future__ import annotations

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import LoggerClient, LoggerError
from .const import API_MAX, CONF_TZ, DOMAIN, FEATURE_RULES, FEATURE_VOICE
from .coordinator import LoggerConfigEntry, LoggerCoordinator
from .rules import LoggerRules
from .services import async_setup_services
from .voice import VoiceSentences, async_remove_sentences, async_setup_intents
from .webhook import async_register as async_register_webhook, async_webhook_id

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.SCENE,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    async_setup_intents(hass)
    return True


# Setup and unload return a bool: HA before 2026.11 refuses anything else (the min HA here is
# 2026.2); custom integrations may keep returning True after it.
async def async_setup_entry(hass: HomeAssistant, entry: LoggerConfigEntry) -> bool:
    tz = entry.options.get(CONF_TZ) or hass.config.time_zone
    client = LoggerClient(async_get_clientsession(hass), entry.data[CONF_URL], tz)
    try:
        about = await client.about()
    except LoggerError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"words": err.words},
        ) from err
    if about.instance_id != entry.unique_id:
        # The address now answers as another Logger: never mix two Loggers' data.
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="wrong_logger",
            translation_placeholders={"found": about.instance_id},
        )
    if about.api > API_MAX:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_api",
            translation_placeholders={"api": str(about.api), "max": str(API_MAX)},
        )
    coordinator = LoggerCoordinator(hass, entry, client, about)
    if FEATURE_RULES in about.features:
        coordinator.rules = LoggerRules(
            hass, entry, client, about, async_webhook_id(hass, entry)
        )
    if FEATURE_VOICE in about.features:
        coordinator.voice = VoiceSentences(hass, entry, coordinator)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator.rules is not None:
        async_register_webhook(hass, entry, coordinator.rules)
        coordinator.rules.async_start()
    if coordinator.voice is not None:
        coordinator.voice.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LoggerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: LoggerConfigEntry) -> None:
    """The entry deleted: the voice sentences it wrote go with it."""
    await async_remove_sentences(hass, entry.entry_id)
