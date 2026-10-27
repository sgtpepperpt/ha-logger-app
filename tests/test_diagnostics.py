"""*Download diagnostics*."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant

from .conftest import URL, setup_entry


async def test_diagnostics(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["entry"]["data"] == {"url": URL}
    assert diagnostics["about"] == {
        k: contract["about"][k] for k in ("api", "instance_id", "code")
    }
    assert diagnostics["status"] == contract["status"]
    assert diagnostics["quick"] == contract["quick"]
    assert diagnostics["alerts"] == contract["alerts"]["alerts"]
