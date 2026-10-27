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
