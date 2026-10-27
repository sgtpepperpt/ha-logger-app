"""*Download diagnostics*."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant

from .conftest import URL, mock_reads, setup_entry


async def test_diagnostics(
    hass: HomeAssistant,
    logger_api: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    await hass.config.async_update(internal_url="http://192.168.1.20:8123")
    await setup_entry(hass, config_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    webhook_id = config_entry.data["webhook_id"]
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    # The webhook id is the one secret: its first 4 characters only.
    assert diagnostics["entry"]["data"] == {
        "url": URL,
        "webhook_id": f"{webhook_id[:4]}…",
    }
    assert webhook_id not in str(diagnostics)
    assert diagnostics["about"] == {
        k: contract["about"][k] for k in ("api", "instance_id", "code", "features")
    }
    assert diagnostics["status"] == contract["status"]
    assert diagnostics["quick"] == contract["quick"]
    assert diagnostics["alerts"] == contract["alerts"]["alerts"]
    rules = diagnostics["rules"]
    assert rules["feature"] is True
    assert rules["webhook"] == {
        "registered": True,
        "url": f"http://192.168.1.20:8123/api/webhook/{webhook_id[:4]}…",
        "accepted_by_logger": True,
        "error": None,
    }
    assert rules["last_cycle"] is not None
    assert rules["last_cycle_error"] is None
    assert rules["rules_rev"] == contract["ha_rules"]["200"]["rev"]
    assert rules["scenes_rev"] == contract["ha_scenes"]["200"]["rev"]
    assert rules["scenes"] == 1
    assert rules["catalog_at"] is not None
    assert set(rules["catalog_counts"]) == set(contract["ha_catalog"]["200"]["counts"])


async def test_diagnostics_without_rules(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """An older Logger (no `features`): nothing of the rules but the switch."""
    about = {k: v for k, v in contract["about"].items() if k != "features"}
    mock_reads(aioclient_mock, contract, about=about)
    await setup_entry(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["entry"]["data"] == {"url": URL}
    assert diagnostics["about"] == {
        **{k: about[k] for k in ("api", "instance_id", "code")},
        "features": [],
    }
    assert diagnostics["rules"] == {"feature": False}
