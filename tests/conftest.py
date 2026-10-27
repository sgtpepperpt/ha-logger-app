"""A Logger answering the contract's examples (tests/fixtures/ha_contract.json) at URL."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
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
    ha: bool = True,
) -> None:
    """Every read Logger answers at `api`, from the contract unless given (+ the rules and
    scenes routes, `mock_ha`, unless `ha` is False)."""
    aioclient_mock.get(f"{api}/about", json=about or contract["about"])
    aioclient_mock.get(f"{api}/status", json=status or contract["status"])
    aioclient_mock.get(
        f"{api}/quick", json=quick if quick is not None else contract["quick"]
    )
    aioclient_mock.get(f"{api}/alerts", json=contract["alerts"])
    if ha:
        mock_ha(aioclient_mock, contract, api=api)


def mock_ha(
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    *,
    actions: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
    scenes: dict[str, Any] | None = None,
    api: str = API,
) -> None:
    """Logger's rules + scenes routes (F39) at `api`: an empty action queue with the
    contract's revs, the contract's rules and scenes, and every write answered as there —
    unless given. Mocks match in order: register a special answer before these.

    The contract's device rule (a zha button) only where a test passes the rules: HA tries
    to import zha for it, slow and pointless in every other test."""
    queue = actions or {**contract["ha_actions"]["200"], "actions": []}
    if rules is None:
        rules = {
            **contract["ha_rules"]["200"],
            "rules": [
                r
                for r in contract["ha_rules"]["200"]["rules"]
                if r["when"]["type"] != "device"
            ],
        }
    aioclient_mock.get(f"{api}/ha/actions", json=queue)
    aioclient_mock.get(f"{api}/ha/rules", json=rules)
    aioclient_mock.get(f"{api}/ha/scenes", json=scenes or contract["ha_scenes"]["200"])
    aioclient_mock.put(f"{api}/ha/catalog", json=contract["ha_catalog"]["200"])
    for route, answer in (
        (r"/ha/actions/\d+/result$", contract["ha_action_result"]["200"]),
        (r"/ha/rules/\d+/state$", contract["ha_rule_state"]["200"]),
        (r"/ha/rules/\d+/fire$", contract["ha_rule_fire"]["200"]),
    ):
        aioclient_mock.post(re.compile(re.escape(api) + route), json=answer)


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


def bodies(
    aioclient_mock: AiohttpClientMocker, method: str, path: str
) -> list[dict[str, Any]]:
    """The JSON bodies sent to `path`, in order."""
    return [json.loads(c[2]) for c in calls(aioclient_mock, method, path)]
