"""A Logger answering the contract's examples (tests/fixtures/ha_contract.json) at URL."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.const import DOMAIN
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

# The two repos meet only here: a copy of Logger's backend/tests/fixtures/ha_contract.json.
CONTRACT: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "ha_contract.json").read_text()
)
URL = "http://logger.test:4173"
API = f"{URL}/api"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def contract() -> dict[str, Any]:
    return deepcopy(CONTRACT)


def mock_reads(
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    *,
    about: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    quick: list[dict[str, Any]] | None = None,
    api: str = API,
) -> None:
    """Every read Logger answers at `api`, from the contract unless given."""
    aioclient_mock.get(f"{api}/about", json=about or contract["about"])
    aioclient_mock.get(f"{api}/status", json=status or contract["status"])
    aioclient_mock.get(
        f"{api}/quick", json=quick if quick is not None else contract["quick"]
    )
    aioclient_mock.get(f"{api}/alerts", json=contract["alerts"])


@pytest.fixture
def logger_api(
    aioclient_mock: AiohttpClientMocker, contract: dict[str, Any]
) -> AiohttpClientMocker:
    mock_reads(aioclient_mock, contract)
    return aioclient_mock


@pytest.fixture
def config_entry(contract: dict[str, Any]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Logger",
        unique_id=contract["about"]["instance_id"],
        data={CONF_URL: URL},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def calls(
    aioclient_mock: AiohttpClientMocker, method: str, path: str
) -> list[tuple[Any, ...]]:
    """The requests made to `path` (method, url, data, headers)."""
    return [
        c
        for c in aioclient_mock.mock_calls
        if c[0].lower() == method.lower() and c[1].path == f"/api{path}"
    ]
