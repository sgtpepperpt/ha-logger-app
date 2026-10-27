"""The client's reading of Logger's answers: success, refusal, no answer."""

from __future__ import annotations

import json
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app.api import (
    LoggerClient,
    LoggerConnectionError,
    LoggerRefused,
    normalize_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import API, URL, calls


@pytest.mark.parametrize(
    ("typed", "url"),
    [
        ("http://logger.test:4173", "http://logger.test:4173"),
        ("http://logger.test:4173/", "http://logger.test:4173"),
        ("http://localhost:5173/api", "http://localhost:5173"),
        ("192.168.1.20:4173", "http://192.168.1.20:4173"),
        ("https://logger.example.org/sub/api/", "https://logger.example.org/sub"),
    ],
)
def test_normalize_url(typed: str, url: str) -> None:
    assert normalize_url(typed) == url


@pytest.mark.parametrize("typed", ["", "ftp://logger.test", "http://"])
def test_normalize_url_refuses(typed: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        normalize_url(typed)


@pytest.fixture
def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> LoggerClient:
    return LoggerClient(async_get_clientsession(hass), URL, "Europe/Lisbon")


async def test_reads_carry_the_client_and_tz(
    client: LoggerClient,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
) -> None:
    aioclient_mock.get(f"{API}/status", json=contract["status"])
    assert (await client.status())["day"] == "2026-10-25"
    [(_, url, body, headers)] = calls(aioclient_mock, "get", "/status")
    assert url.query["tz"] == "Europe/Lisbon"
    assert body is None
    assert headers == {"X-Logger-Client": "ha"}


async def test_refusal_keeps_loggers_body(
    client: LoggerClient,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
) -> None:
    aioclient_mock.post(
        f"{API}/quick/20/fire", status=409, json=contract["fire"]["409"]
    )
    with pytest.raises(LoggerRefused) as err:
        await client.fire(20)
    assert err.value.words == "already taken: Omega-3, Zinc"
    assert err.value.status == 409
    assert err.value.body["already"] == ["Omega-3", "Zinc"]
    [(_, _, body, _)] = calls(aioclient_mock, "post", "/quick/20/fire")
    assert json.loads(body) == {"tz": "Europe/Lisbon"}


async def test_tablet_502_with_words_is_a_refusal(
    client: LoggerClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Logger's own 502 (the tablet didn't wake) has words; a proxy's 502 has none."""
    aioclient_mock.post(
        f"{API}/tablet/wake",
        status=502,
        json={"ok": False, "outcome": "failed", "words": "The tablet didn't wake."},
    )
    with pytest.raises(LoggerRefused, match="didn't wake"):
        await client.tablet_wake()


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 502, "text": "<html>Bad Gateway</html>"},
        {"status": 503, "json": {"maintenance": True}},
        {"status": 200, "text": "<html>Sign in</html>"},
        {"status": 302, "headers": {"Location": "https://auth.example.org/"}},
        {"exc": aiohttp.ClientConnectionError("refused")},
        {"exc": TimeoutError()},
    ],
)
async def test_no_answer(
    client: LoggerClient, aioclient_mock: AiohttpClientMocker, answer: dict[str, Any]
) -> None:
    aioclient_mock.get(f"{API}/status", **answer)
    with pytest.raises(LoggerConnectionError):
        await client.status()


async def test_about_must_be_loggers(
    client: LoggerClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{API}/about", json={"api": "1", "instance_id": "x"})
    with pytest.raises(LoggerConnectionError):
        await client.about()


async def test_quick_skips_malformed_rows(
    client: LoggerClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{API}/quick", json=[{"id": 1, "name": "a"}, {"name": "no id"}, "junk"]
    )
    assert await client.quick() == [{"id": 1, "name": "a"}]


@pytest.mark.parametrize(
    ("sent", "features"),
    [
        ({"features": ["rules"]}, ("rules",)),
        ({"features": ["rules", 3, None, "later"]}, ("rules", "later")),
        ({"features": "rules"}, ()),
        ({}, ()),  # an older Logger
    ],
)
async def test_about_features(
    client: LoggerClient,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    sent: dict[str, Any],
    features: tuple[str, ...],
) -> None:
    about = {k: v for k, v in contract["about"].items() if k != "features"}
    aioclient_mock.get(f"{API}/about", json={**about, **sent})
    assert (await client.about()).features == features


async def test_ha_routes_send_the_contracts_bodies(
    client: LoggerClient,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
) -> None:
    """The `/ha/*` writes carry exactly the contract's bodies — no `tz` — and every
    request names its client and sends no Origin."""
    aioclient_mock.put(f"{API}/ha/catalog", json=contract["ha_catalog"]["200"])
    aioclient_mock.get(f"{API}/ha/actions", json=contract["ha_actions"]["200"])
    aioclient_mock.post(
        f"{API}/ha/actions/41/result", json=contract["ha_action_result"]["200"]
    )
    aioclient_mock.post(
        f"{API}/ha/actions/42/result", json=contract["ha_action_result"]["200"]
    )
    aioclient_mock.get(f"{API}/ha/rules", json=contract["ha_rules"]["200"])
    aioclient_mock.post(f"{API}/ha/rules/3/fire", json=contract["ha_rule_fire"]["200"])
    aioclient_mock.post(
        f"{API}/ha/rules/7/state", json=contract["ha_rule_state"]["200"]
    )
    aioclient_mock.get(f"{API}/ha/scenes", json=contract["ha_scenes"]["200"])

    catalog = contract["ha_catalog"]["request"]
    assert await client.ha_catalog(catalog) == contract["ha_catalog"]["200"]
    assert await client.ha_actions() == contract["ha_actions"]["200"]
    await client.ha_action_result(41, words="Kitchen flashed")
    await client.ha_action_result(42, error="light.kitchen is unavailable")
    assert await client.ha_rules() == contract["ha_rules"]["200"]
    fire = contract["ha_rule_fire"]["request"]
    assert (
        await client.ha_rule_fire(3, fire["at"], fire["event"])
        == (contract["ha_rule_fire"]["200"])
    )
    await client.ha_rule_state(7, contract["ha_rule_state"]["request"]["error"])
    assert await client.ha_scenes() == contract["ha_scenes"]["200"]

    assert [json.loads(c[2]) for c in calls(aioclient_mock, "put", "/ha/catalog")] == [
        catalog
    ]
    [(_, _, ok, _)] = calls(aioclient_mock, "post", "/ha/actions/41/result")
    [(_, _, failed, _)] = calls(aioclient_mock, "post", "/ha/actions/42/result")
    assert json.loads(ok) == contract["ha_action_result"]["request_ok"]
    assert json.loads(failed) == contract["ha_action_result"]["request_failed"]
    [(_, rules_url, _, _)] = calls(aioclient_mock, "get", "/ha/rules")
    assert rules_url.query["kind"] == "from_ha"
    [(_, _, fired, _)] = calls(aioclient_mock, "post", "/ha/rules/3/fire")
    assert json.loads(fired) == fire
    [(_, _, state, _)] = calls(aioclient_mock, "post", "/ha/rules/7/state")
    assert json.loads(state) == contract["ha_rule_state"]["request"]
    for _, _, body, headers in aioclient_mock.mock_calls:
        assert headers["X-Logger-Client"] == "ha"
        assert "Origin" not in headers
        if body is not None:
            assert headers["Content-Type"] == "application/json"
            assert "tz" not in json.loads(body)


@pytest.mark.parametrize(
    ("status", "error"),
    [(409, LoggerRefused), (404, LoggerRefused), (502, LoggerConnectionError)],
)
async def test_ha_result_answers(
    client: LoggerClient,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    status: int,
    error: type[Exception],
) -> None:
    """409 = answered already (the contract's words), 404 = gone; a proxy's 502 = no
    answer."""
    answer = (
        {"json": contract["ha_action_result"]["409"]}
        if status == 409
        else {"json": {"ok": False, "error": "No action #9"}}
        if status == 404
        else {"text": "<html>Bad Gateway</html>"}
    )
    aioclient_mock.post(f"{API}/ha/actions/9/result", status=status, **answer)
    with pytest.raises(error) as err:
        await client.ha_action_result(9, words="done")
    assert err.value.status == status
