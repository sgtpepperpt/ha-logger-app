"""Adding a Logger by its address, reconfiguring it, its options."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.const import CONF_ALERTS, CONF_TZ, DOMAIN
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import API, URL, mock_reads, setup_entry


async def start(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


@pytest.mark.parametrize(
    "typed", [URL, f"{URL}/", f"{URL}/api", "logger.test:4173", f"  {URL}  "]
)
async def test_user_flow(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    typed: str,
) -> None:
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: typed}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Logger"
    assert result["data"] == {CONF_URL: URL}
    assert result["result"].unique_id == contract["about"]["instance_id"]


@pytest.mark.parametrize(
    "answer",
    [
        {"exc": TimeoutError()},
        {"status": 502, "text": "<html>Bad Gateway</html>"},
        # The public address: its sign-in page (a redirect, or the page itself).
        {"status": 302, "headers": {"Location": "https://auth.example.org/"}},
        {"status": 200, "text": "<!doctype html><title>Sign in</title>"},
        {"status": 200, "json": {"something": "else"}},
    ],
)
async def test_cannot_connect_then_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    answer: dict[str, Any],
) -> None:
    aioclient_mock.get(f"{API}/about", **answer)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    mock_reads(aioclient_mock, contract)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_invalid_url(
    hass: HomeAssistant, logger_api: AiohttpClientMocker
) -> None:
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "ftp://logger.test"}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}


async def test_already_configured_updates_the_address(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
) -> None:
    """The same Logger (instance_id) at a new address: one entry, the address updated."""
    old = MockConfigEntry(
        domain=DOMAIN,
        unique_id=contract["about"]["instance_id"],
        data={CONF_URL: "http://192.168.1.9:4173"},
    )
    old.add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert old.data[CONF_URL] == URL


async def test_unsupported_api(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, contract: dict[str, Any]
) -> None:
    mock_reads(aioclient_mock, contract, about={**contract["about"], "api": 2})
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_api"
    assert result["description_placeholders"] == {"api": "2", "max": "1"}


async def test_reconfigure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    new = "http://192.168.1.20:4173"
    mock_reads(aioclient_mock, contract, api=f"{new}/api")

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: new}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_URL] == new
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED  # reloaded on the new address
    assert config_entry.runtime_data.client.url == new


async def test_reconfigure_to_another_logger(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_reads(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    other = "http://192.168.1.30:4173"
    aioclient_mock.get(
        f"{other}/api/about", json={**contract["about"], "instance_id": "other"}
    )
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: other}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_logger"
    assert config_entry.data[CONF_URL] == URL


async def test_options_reload(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Saved options reload the entry: the new zone is what `/status` is asked in."""
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 30.0, CONF_TZ: "Not/AZone", CONF_ALERTS: False},
    )
    assert result["errors"] == {CONF_TZ: "invalid_tz"}

    logger_api.mock_calls.clear()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 30.0, CONF_TZ: "Europe/Lisbon", CONF_ALERTS: False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options == {
        CONF_SCAN_INTERVAL: 30,
        CONF_TZ: "Europe/Lisbon",
        CONF_ALERTS: False,
    }
    assert config_entry.state is ConfigEntryState.LOADED
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 30
    status_reads = [c for c in logger_api.mock_calls if c[1].path == "/api/status"]
    assert status_reads[-1][1].query["tz"] == "Europe/Lisbon"
    assert not [c for c in logger_api.mock_calls if c[1].path == "/api/alerts"]
