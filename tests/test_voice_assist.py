"""End to end through Home Assistant's own Assist agent: the sentences file the integration writes
is loaded by the `conversation` integration, a sentence said reaches `LoggerCommand` (or an
answer) with its slots, Logger is asked, Assist speaks. Needs Assist's own requirements (hassil,
home-assistant-intents: requirements_test.txt); without them these skip."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import bodies, calls
from .test_voice import mock_voice, started, voice_entry

pytest.importorskip("hassil")
pytest.importorskip("home_assistant_intents")


@pytest.fixture
async def assist(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    tmp_path: Path,
) -> AiohttpClientMocker:
    """Assist loaded, the integration set up with its voice sentences on."""
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "configuration.yaml").write_text(
        "", encoding="utf-8"
    )  # Assist's reload reads it
    mock_voice(aioclient_mock, contract)
    assert await async_setup_component(hass, "homeassistant", {})  # exposed entities
    assert await async_setup_component(hass, "conversation", {})
    with patch("custom_components.logger_app.voice.SHARE_DIR", str(tmp_path / "none")):
        await started(hass, voice_entry(contract))
    assert (tmp_path / "custom_sentences" / "pt" / "logger.yaml").exists()
    return aioclient_mock


async def converse(hass: HomeAssistant, text: str, lang: str) -> Any:
    from homeassistant.components import conversation

    result = await conversation.async_converse(hass, text, None, Context(), lang)
    return result.response


async def test_a_portuguese_command_through_assist(
    hass: HomeAssistant, assist: AiohttpClientMocker
) -> None:
    response = await converse(hass, "Regista uma chávena de café.", "pt")
    assert response.speech["plain"]["speech"] == "Registado: Café 1 chávena."
    [(_, url, _, _)] = calls(assist, "get", "/command/parse")
    assert url.query["text"] == "Regista uma chávena de café"
    assert bodies(assist, "post", "/command/run")[0]["intent"] == "food"


async def test_free_text_and_a_measure_through_assist(
    hass: HomeAssistant, assist: AiohttpClientMocker
) -> None:
    await converse(hass, "lembra-me de ligar à mãe amanhã", "pt")
    await converse(hass, "o meu peso é 62,9", "pt")
    sent = [c[1].query["text"] for c in calls(assist, "get", "/command/parse")]
    assert sent == ["lembra-me de ligar à mãe amanhã", "peso 62.9"]


async def test_an_answer_through_assist(
    hass: HomeAssistant, assist: AiohttpClientMocker
) -> None:
    response = await converse(hass, "how much is free today", "en")
    assert response.speech["plain"]["speech"] == "75 calories free today."
    response = await converse(hass, "quanto tenho livre hoje", "pt")
    assert response.speech["plain"]["speech"] == "Tens 75 calorias livres hoje."


async def test_the_house_s_sentences_stay_home_assistant_s(
    hass: HomeAssistant, assist: AiohttpClientMocker
) -> None:
    response = await converse(hass, "what time is it", "en")
    assert "calories" not in response.speech["plain"]["speech"]
    assert not calls(assist, "get", "/command/parse")


async def test_turning_the_sentences_off_reloads_assist(
    hass: HomeAssistant, assist: AiohttpClientMocker
) -> None:
    """Assist caches its sentences: the integration's `conversation.reload` (the real one here)
    makes it forget them once the file is gone."""
    from custom_components.logger_app.const import CONF_VOICE, DOMAIN

    await converse(hass, "regista uma chávena de café", "pt")
    [entry] = hass.config_entries.async_entries(DOMAIN)
    hass.config_entries.async_update_entry(entry, options={CONF_VOICE: False})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assist.mock_calls.clear()
    response = await converse(hass, "regista uma chávena de café", "pt")
    assert (
        response.error_code is not None
    )  # no intent matched: Assist's own "didn't understand"
    assert not calls(assist, "get", "/command/parse")
