"""Setup, unload, and the identity checks at setup."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import API, URL, mock_reads, setup_entry


async def test_setup_and_unload(
    hass: HomeAssistant, logger_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    [device] = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )
    assert device.identifiers == {(DOMAIN, config_entry.unique_id)}
    assert device.name == "Logger"
    assert device.sw_version == "3f2a9c1d7e4b"  # about.code
    assert device.configuration_url == URL

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_not_ready_while_logger_is_down(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.get(f"{API}/about", status=502, text="<html>Bad Gateway</html>")
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_another_logger_at_the_address(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """A restored copy elsewhere is the same Logger; a different instance_id never is."""
    mock_reads(
        aioclient_mock,
        contract,
        about={**contract["about"], "instance_id": "another-logger"},
    )
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert "another-logger" in (config_entry.reason or "")


async def test_newer_api_is_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract, about={**contract["about"], "api": 2})
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_older_api_is_fine(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract, about={**contract["about"], "api": 0})
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
