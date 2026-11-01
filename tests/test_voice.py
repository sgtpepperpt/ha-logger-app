"""Voice (voice.py): Assist's `LoggerCommand` and answers through Logger, and the sentences file.

The commands: Logger's reading first (`GET /command/parse`), a write only for an eager reading of
the sentence family's kind (`POST /command/run {text, intent, source: voice}`), Logger's words
spoken (Portuguese from the reading), everything else said with nothing written. The file: written
for pt and en where Home Assistant and Speech-to-Phrase read it (option on), rewritten only when
Logger's words change, never over a file that isn't this integration's, removed with the option
off or the entry gone; Assist reloaded after a change."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.logger_app import sentences
from custom_components.logger_app.const import (
    CONF_VOICE,
    DOMAIN,
    EVENT_FIRED,
    SENTENCES_FILE,
)
from homeassistant.const import CONF_URL
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util

from .conftest import API, URL, bodies, calls, mock_reads, setup_entry


def contract_parse(contract: dict[str, Any], name: str = "eager") -> dict[str, Any]:
    return deepcopy(contract["command_parse"][name])


def mock_voice(
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    *,
    parse: dict[str, Any] | None = None,
    run: tuple[int, dict[str, Any]] | None = None,
    vocab: dict[str, Any] | None = None,
    about: dict[str, Any] | None = None,
) -> None:
    """Logger's reads (conftest.mock_reads) + its command routes: the contract's eager reading,
    its 201, its vocabulary — unless given."""
    status, answer = run or (201, contract["command_run"]["201"])
    aioclient_mock.get(f"{API}/command/parse", json=parse or contract_parse(contract))
    aioclient_mock.post(f"{API}/command/run", status=status, json=answer)
    aioclient_mock.get(f"{API}/command/vocab", json=vocab or contract["command_vocab"])
    mock_reads(aioclient_mock, contract, about=about)


async def say(
    hass: HomeAssistant,
    said: str | None,
    lang: str = "pt",
    family: str | None = "log",
    intent_type: str = sentences.INTENT_COMMAND,
    **slots: Any,
) -> intent.IntentResponse:
    """What Home Assistant's agent does once a sentence matched: the intent with its slots."""
    if family is not None:
        slots["family"] = family
    return await intent.async_handle(
        hass,
        "conversation",
        intent_type,
        {name: {"value": value} for name, value in slots.items()},
        text_input=said,
        context=Context(),
        language=lang,
    )


def spoken(response: intent.IntentResponse) -> str:
    return response.speech["plain"]["speech"]


# ── the commands ─────────────────────────────────────────────────────────────


async def test_an_eager_reading_is_written_and_said(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    mock_voice(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    events = async_capture_events(hass, EVENT_FIRED)

    response = await say(hass, "Regista uma chávena de café.")
    assert response.response_type is intent.IntentResponseType.ACTION_DONE
    assert spoken(response) == "Registado: Café 1 chávena."
    [(_, url, _, headers)] = calls(aioclient_mock, "get", "/command/parse")
    assert (
        url.query["text"] == "Regista uma chávena de café"
    )  # the voice's full stop dropped
    assert headers["X-Logger-Client"] == "ha"
    assert bodies(aioclient_mock, "post", "/command/run") == [
        {
            "text": "Regista uma chávena de café",
            "intent": "food",
            "source": "voice",
            "tz": hass.config.time_zone,
        }
    ]
    assert events[0].data == {
        "kind": "command",
        "name": "food",
        "words": contract["command_run"]["201"]["words"],
    }

    response = await say(hass, "regista uma chávena de café", lang="en")
    assert spoken(response) == "Logged Café 1 chávena"


@pytest.mark.parametrize(
    ("name", "lang", "words"),
    [
        ("needs", "pt", "Diz outra vez com a quantidade. Não registei nada."),
        ("needs", "en", "Say it again with the amount. Nothing written."),
        (
            "warning",
            "en",
            "Leite de magnésia: already taken, next at 20:00. Nothing written.",
        ),
    ],
)
async def test_a_reading_that_needs_more_writes_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    name: str,
    lang: str,
    words: str,
) -> None:
    mock_voice(aioclient_mock, contract, parse=contract_parse(contract, name))
    await setup_entry(hass, config_entry)
    response = await say(hass, "x", lang=lang)
    assert response.response_type is intent.IntentResponseType.ERROR
    assert spoken(response) == words
    assert not calls(aioclient_mock, "post", "/command/run")


async def test_a_guess_or_another_kind_writes_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """Logger not sure (a guess under its Enter's bar), or a reading of a kind the sentence
    didn't ask for (a `buy` sentence read as food): said, never written."""
    guess = {**contract_parse(contract), "eager": False, "confidence": 0.45}
    mock_voice(aioclient_mock, contract, parse=guess)
    await setup_entry(hass, config_entry)
    response = await say(hass, "regista um café")
    assert spoken(response) == (
        "Não tenho a certeza: Café 1 chávena? Não registei nada. Diz o nome completo."
    )
    response = await say(hass, "comprar café", family="buy", lang="en")
    assert spoken(response) == "Not sure what to do with that. Nothing written."
    assert not calls(aioclient_mock, "post", "/command/run")


async def test_the_family_s_kind_among_the_alternatives_is_kept(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    reading = contract_parse(contract)
    task = reading["alternatives"][0]
    shopping = {
        **task,
        "intent": "shopping",
        "eager": True,
        "head": "Buy",
        "title": "café",
    }
    mock_voice(
        aioclient_mock,
        contract,
        parse={**reading, "alternatives": [task, shopping]},
        run=(201, {"ok": True, "intent": "shopping", "words": "On the list: café"}),
    )
    await setup_entry(hass, config_entry)
    response = await say(hass, "comprar café", family="buy")
    assert spoken(response) == "Na lista de compras: café."
    assert bodies(aioclient_mock, "post", "/command/run")[0]["intent"] == "shopping"


@pytest.mark.parametrize(
    ("family", "said", "slots", "sent"),
    [
        ("remind", "tarefa marcar dentista", {}, "tarefa: marcar dentista"),
        ("note", "Anota, dormi mal.", {}, "nota: dormi mal"),
        ("measure", "o meu peso é 62,9", {"logger_kg": 62.9}, "peso 62.9"),
        (
            "measure",
            "a minha tensão é 120 por 80",
            {"logger_sys": 120.0, "logger_dia": 80.0},
            "tensão 120/80",
        ),
        (
            "measure",
            "A minha tensão é 131 por 84.",
            {"logger_measure": "tensão", "logger_text": "é 131 por 84"},
            "tensão é 131/84",
        ),
        (
            "spend",
            "gastei 3 euros e 5 cêntimos no café",
            {"logger_euros": 3.0, "logger_cents": 5.0, "logger_money_item": "Café"},
            "gastei 3,05 € em Café",
        ),
    ],
)
async def test_what_a_voice_cant_say_is_put_back(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    family: str,
    said: str,
    slots: dict[str, Any],
    sent: str,
) -> None:
    mock_voice(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    await say(hass, said, family=family, **slots)
    [(_, url, _, _)] = calls(aioclient_mock, "get", "/command/parse")
    assert url.query["text"] == sent


async def test_an_llm_s_call_names_its_text(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    """An LLM agent with the Assist API calls the intent as a tool: `text` (+ `family`)."""
    mock_voice(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    response = await say(
        hass, "please log my coffee", lang="en", family=None, text="a cup of café"
    )
    assert spoken(response) == "Logged Café 1 chávena"
    [(_, url, _, _)] = calls(aioclient_mock, "get", "/command/parse")
    assert url.query["text"] == "a cup of café"


@pytest.mark.parametrize(
    ("status", "answer", "words"),
    [
        (400, None, "Diz outra vez com o nível. Não registei nada."),
        (
            403,
            {"ok": False, "error": "Tasks can't be written here."},
            "O Logger recusou: Tasks can't be written here.",
        ),
        (503, {}, "Não consigo falar com o Logger agora."),
    ],
)
async def test_a_refused_or_failed_run(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    status: int,
    answer: dict[str, Any] | None,
    words: str,
) -> None:
    mock_voice(
        aioclient_mock,
        contract,
        run=(status, answer if answer is not None else contract["command_run"]["400"]),
    )
    await setup_entry(hass, config_entry)
    events = async_capture_events(hass, EVENT_FIRED)
    response = await say(hass, "sinto dor de cabeça")
    assert response.response_type is intent.IntentResponseType.ERROR
    assert spoken(response) == words
    assert not events


async def test_without_a_logger_offering_voice(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
) -> None:
    about = {**contract["about"], "features": ["rules", "calendar"]}
    mock_voice(aioclient_mock, contract, about=about)
    await setup_entry(hass, config_entry)
    response = await say(hass, "regista um café")
    assert spoken(response) == "Este Logger ainda não tem comandos de voz. Atualiza-o."
    assert not calls(aioclient_mock, "get", "/command/parse")
    assert config_entry.runtime_data.voice is None

    await hass.config_entries.async_unload(config_entry.entry_id)
    response = await say(
        hass, "how much is free", lang="en", family=None, intent_type="LoggerFreeToday"
    )
    assert spoken(response) == "Logger isn't set up in Home Assistant."


# ── the answers ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("intent_type", "slots", "lang", "words"),
    [
        (sentences.INTENT_FREE, {}, "pt", "Tens 75 calorias livres hoje."),
        (sentences.INTENT_FREE, {}, "en", "75 calories free today."),
        (
            sentences.INTENT_DUE,
            {},
            "en",
            "Overdue: Omega-3. Due now: Vitamin D and Stretch. Tasks: 1 overdue and 1 today.",
        ),
        (
            sentences.INTENT_NEXT_TASK,
            {"logger_list": "deliveries"},
            "pt",
            "Não há tarefas para hoje na lista deliveries.",
        ),
    ],
)
async def test_the_answers(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    intent_type: str,
    slots: dict[str, Any],
    lang: str,
    words: str,
) -> None:
    mock_voice(aioclient_mock, contract)
    await setup_entry(hass, config_entry)
    reads = len(calls(aioclient_mock, "get", "/status"))
    response = await say(
        hass, None, lang=lang, family=None, intent_type=intent_type, **slots
    )
    assert response.response_type is intent.IntentResponseType.QUERY_ANSWER
    assert spoken(response) == words
    assert len(calls(aioclient_mock, "get", "/status")) == reads + 1  # read fresh


# ── the sentences file ───────────────────────────────────────────────────────


@pytest.fixture
def places(hass: HomeAssistant, tmp_path: Path) -> Iterator[dict[str, Path]]:
    """Home Assistant's config in a temporary folder, a /share with Speech-to-Phrase's folder."""
    hass.config.config_dir = str(tmp_path / "config")
    share = tmp_path / "share"
    share.mkdir()
    stp = share / "speech-to-phrase" / "custom_sentences"
    with (
        patch("custom_components.logger_app.voice.SHARE_DIR", str(share)),
        patch("custom_components.logger_app.voice.SPEECH_TO_PHRASE_DIR", str(stp)),
    ):
        yield {"ha": tmp_path / "config" / "custom_sentences", "stp": stp}


@pytest.fixture
def reloads(hass: HomeAssistant) -> list[ServiceCall]:
    """Assist's `conversation.reload`, counted (the real one needs hassil's whole agent)."""
    seen: list[ServiceCall] = []

    async def reload(call: ServiceCall) -> None:
        seen.append(call)

    hass.services.async_register("conversation", "reload", reload)
    return seen


def voice_entry(contract: dict[str, Any], on: bool = True) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Logger",
        unique_id=contract["about"]["instance_id"],
        data={CONF_URL: URL},
        options={CONF_VOICE: on},
    )


async def started(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await setup_entry(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)


def files(places: dict[str, Path]) -> dict[str, str]:
    return {
        f"{where}/{folder}": (base / folder / SENTENCES_FILE).read_text(
            encoding="utf-8"
        )
        for where, base in places.items()
        for folder in ("en", "pt", "pt_PT")
        if (base / folder / SENTENCES_FILE).exists()
    }


async def test_on_writes_both_languages_where_both_read_them(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    mock_voice(aioclient_mock, contract)
    entry = voice_entry(contract)
    await started(hass, entry)
    written = files(places)
    # Speech-to-Phrase finds Portuguese by its model's language (pt_PT), else by pt.
    assert sorted(written) == ["ha/en", "ha/pt", "stp/en", "stp/pt", "stp/pt_PT"]
    for name, text in written.items():
        lang = name.split("/")[1][:2]
        assert text == sentences.build(contract["command_vocab"], lang, "2026.11.0")
    assert len(reloads) == 1
    voice = entry.runtime_data.voice.diagnostics()
    assert (voice["on"], len(voice["written"]), voice["error"]) == (True, 5, None)
    assert voice["counts"]["food"] == len(
        sentences.lists(contract["command_vocab"], "pt")["logger_food"]["values"]
    )

    # Refresh: Logger's words read again; the same words, nothing rewritten, no reload.
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.logger_refresh"}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls(aioclient_mock, "get", "/command/vocab")) == 2
    assert len(entry.runtime_data.voice.diagnostics()["unchanged"]) == 5
    assert len(reloads) == 1


async def test_new_words_rewrite_the_files_once_a_day(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    mock_voice(aioclient_mock, contract)
    entry = voice_entry(contract)
    await started(hass, entry)
    vocab = deepcopy(contract["command_vocab"])
    vocab["foods"].append("Granola")
    aioclient_mock.clear_requests()
    mock_voice(aioclient_mock, contract, vocab=vocab)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=24, seconds=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Granola" in files(places)["ha/pt"]
    assert len(reloads) == 2


async def test_a_file_not_ours_is_never_touched(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    mine = places["ha"] / "pt" / SENTENCES_FILE
    mine.parent.mkdir(parents=True)
    mine.write_text("language: pt\nintents: {}\n", encoding="utf-8")
    mock_voice(aioclient_mock, contract)
    entry = voice_entry(contract)
    await started(hass, entry)
    assert mine.read_text(encoding="utf-8") == "language: pt\nintents: {}\n"
    assert entry.runtime_data.voice.diagnostics()["kept"] == [str(mine)]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert mine.exists()


async def test_off_removes_ours_and_so_does_removing_the_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    mock_voice(aioclient_mock, contract)
    entry = voice_entry(contract)
    await started(hass, entry)
    assert len(files(places)) == 5

    hass.config_entries.async_update_entry(entry, options={CONF_VOICE: False})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert files(places) == {}
    assert len(reloads) == 2
    assert entry.runtime_data.voice.diagnostics()["on"] is False

    hass.config_entries.async_update_entry(entry, options={CONF_VOICE: True})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(files(places)) == 5
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert files(places) == {}


async def test_off_by_default_writes_nothing(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    config_entry: MockConfigEntry,
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    mock_voice(aioclient_mock, contract)
    await started(hass, config_entry)
    assert files(places) == {}
    assert not calls(aioclient_mock, "get", "/command/vocab")
    assert reloads == []


async def test_without_share_only_home_assistant_s_folder(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
    tmp_path: Path,
) -> None:
    mock_voice(aioclient_mock, contract)
    with patch("custom_components.logger_app.voice.SHARE_DIR", str(tmp_path / "none")):
        await started(hass, voice_entry(contract))
    assert sorted(files(places)) == ["ha/en", "ha/pt"]


async def test_logger_unreachable_keeps_the_files_and_says_why(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    aioclient_mock.get(f"{API}/command/vocab", status=502, text="<html>proxy</html>")
    mock_voice(aioclient_mock, contract)
    entry = voice_entry(contract)
    await started(hass, entry)
    assert files(places) == {}
    assert entry.runtime_data.voice.diagnostics()["error"] == "HTTP 502"


async def test_diagnostics_carry_the_voice_state(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    contract: dict[str, Any],
    places: dict[str, Path],
    reloads: list[ServiceCall],
) -> None:
    from custom_components.logger_app.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    mock_voice(aioclient_mock, contract)
    entry = voice_entry(contract)
    await started(hass, entry)
    found = await async_get_config_entry_diagnostics(hass, entry)
    assert found["voice"]["feature"] is True
    assert found["voice"]["on"] is True
    assert json.dumps(found["voice"])  # serialisable
