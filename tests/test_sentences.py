"""The sentences file (sentences.py): built from Logger's words, the same bytes for the same words,
names as a voice says them — and, with hassil (Home Assistant's and Speech-to-Phrase's sentence
matcher, in requirements_test.txt), every sample of the contract (`voice.sentences`) matched to
its family and turned into the line Logger reads (voice.command_text), the answers matched, free
text taken by the wildcards, the house's own sentences left alone."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml

from custom_components.logger_app import sentences
from custom_components.logger_app.voice import command_text

from .conftest import CONTRACT

VOCAB: dict[str, Any] = CONTRACT["command_vocab"]


def test_build_is_marked_stable_and_follows_the_words() -> None:
    for lang in sentences.LANGUAGES:
        text = sentences.build(VOCAB, lang, "2026.11.0")
        assert sentences.is_ours(text)
        assert text == sentences.build(VOCAB, lang, "2026.11.0")
        first = text.splitlines()[0]
        assert first.startswith(f"{sentences.MARKER} · {lang} · ")
        doc = yaml.safe_load(text)
        assert doc["language"] == lang
        assert set(doc["intents"]) == {
            sentences.INTENT_COMMAND,
            sentences.INTENT_FREE,
            sentences.INTENT_DUE,
            sentences.INTENT_NEXT_TASK,
        }
        families = [
            d["slots"]["family"] for d in doc["intents"]["LoggerCommand"]["data"]
        ]
        assert families == list(sentences.FAMILIES)
    # New words, new digest on the first line (Speech-to-Phrase hashes only a file's first 8 KB).
    more = deepcopy(VOCAB)
    more["foods"].append("Granola")
    assert (
        sentences.build(more, "pt").splitlines()[0]
        != sentences.build(VOCAB, "pt").splitlines()[0]
    )
    assert not sentences.is_ours("language: pt\n")


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("Café", "Café"),
        ("Iogurte (natural)", "Iogurte natural"),
        ("Pão {forma} [x] <y> | z ; w", "Pão forma x y z w"),
        ("Leite 0% gordura", "Leite 0 gordura"),
        ("caixa da manhã", "caixa da manhã"),
        ("home <-> office", "home - office"),
        ("100%", None),
        ("", None),
        (None, None),
        ("a b c d e f g h i", None),  # too long to be said whole
    ],
)
def test_spoken(name: Any, said: str | None) -> None:
    assert sentences.spoken(name) == said


def test_lists_from_the_vocabulary() -> None:
    lists = sentences.lists(VOCAB, "pt")
    foods = lists["logger_food"]["values"]
    assert {"in": "(Café|Cafés)", "out": "Café"} in foods  # one word: its plural too
    assert "Pão de forma" in foods
    assert "Pequeno almoço" in foods  # a meal's nickname
    assert lists["logger_item"]["values"] == [
        {"in": "(Leite|Leites)", "out": "Leite"},
        "Pão",
        {"in": "(Detergente|Detergentes)", "out": "Detergente"},
    ]
    units = [
        u if isinstance(u, str) else u["out"] for u in lists["logger_unit"]["values"]
    ]
    assert units[:5] == [
        "chávena",
        "caneca",
        "copo",
        "fatia",
        "unidade",
    ]  # `un`, said out
    assert "colheres" in units  # then the language's own
    assert lists["logger_list"]["values"][2] == "home - office"
    assert lists["logger_text"] == {"wildcard": True}
    assert lists["logger_kg"]["range"]["fractions"] == "tenths"
    # A food said without the numbers its name ends in; products keep theirs.
    dated = sentences.lists(
        {
            "foods": ["Grão cozido (16/8/2026)", "Quadrado Kaoka 85%"],
            "products": ["B12"],
        },
        "pt",
    )
    assert dated["logger_food"]["values"] == ["Grão cozido", "Quadrado Kaoka"]
    assert dated["logger_product"]["values"] == ["B12"]
    assert sentences.counts(VOCAB)["food"] == len(foods)


def test_nothing_from_logger_still_makes_a_file() -> None:
    doc = yaml.safe_load(sentences.build({}, "en"))
    assert doc["lists"]["logger_food"] == {"values": []}
    assert doc["lists"]["logger_unit"]["values"]  # the language's own portion words


# ── matched as Home Assistant's agent and Speech-to-Phrase match them (hassil) ──
# hassil comes with requirements_test.txt (the minimum HA's pin); without it these skip.


def best(text: str, lang: str, vocab: dict[str, Any] = VOCAB) -> Any:
    hassil = pytest.importorskip("hassil")
    from hassil.recognize import recognize_best

    found = hassil.Intents.from_dict(yaml.safe_load(sentences.build(vocab, lang)))
    return recognize_best(text, found)


@pytest.mark.parametrize(
    "sample", CONTRACT["voice"]["sentences"], ids=lambda s: f"{s['lang']}-{s['said']}"
)
def test_every_sample_is_its_family_and_its_line(sample: dict[str, Any]) -> None:
    result = best(sample["said"], sample["lang"])
    assert result is not None
    slots = {name: entity.value for name, entity in result.entities.items()}
    family = slots.pop("family")
    assert (result.intent.name, family) == (sentences.INTENT_COMMAND, sample["family"])
    assert command_text(family, sample["said"], slots, sample["lang"]) == sample["text"]


@pytest.mark.parametrize(
    ("text", "lang", "intent", "slots"),
    [
        ("quanto tenho livre hoje", "pt", "LoggerFreeToday", {}),
        ("quantas calorias me restam", "pt", "LoggerFreeToday", {}),
        ("o que falta hoje", "pt", "LoggerWhatsDue", {}),
        ("qual é a próxima tarefa", "pt", "LoggerNextTask", {}),
        (
            "próxima tarefa da lista errands",
            "pt",
            "LoggerNextTask",
            {"logger_list": "errands"},
        ),
        ("how much is free today", "en", "LoggerFreeToday", {}),
        ("what's due", "en", "LoggerWhatsDue", {}),
        ("what's my next task", "en", "LoggerNextTask", {}),
        (
            "next task in the Garden list",
            "en",
            "LoggerNextTask",
            {"logger_list": "Garden"},
        ),
    ],
)
def test_the_answers(text: str, lang: str, intent: str, slots: dict[str, Any]) -> None:
    result = best(text, lang)
    assert result is not None
    assert result.intent.name == intent
    assert {name: entity.value for name, entity in result.entities.items()} == slots


@pytest.mark.parametrize(
    ("text", "lang", "family"),
    [
        (
            "regista arroz de pato",
            "pt",
            "log",
        ),  # no such food: Whisper's free text, whole
        ("comprar pilhas AA no supermercado", "pt", "buy"),
        ("lembra-me de regar as plantas", "pt", "remind"),
        ("o meu peso é sessenta e dois", "pt", "measure"),
        ("i ate a bowl of pho", "en", "log"),
        ("remember to water the plants", "en", "remind"),
    ],
)
def test_free_text_goes_to_its_family(text: str, lang: str, family: str) -> None:
    result = best(text, lang)
    assert result is not None
    assert result.intent.name == sentences.INTENT_COMMAND
    assert result.entities["family"].value == family


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("liga a luz da cozinha", "pt"),
        ("que horas são", "pt"),
        ("turn on the kitchen light", "en"),
        ("what time is it", "en"),
    ],
)
def test_the_house_s_own_sentences_are_left_alone(text: str, lang: str) -> None:
    assert best(text, lang) is None


def test_an_empty_vocabulary_parses() -> None:
    result = best("quanto tenho livre hoje", "pt", {})
    assert result is not None
    assert result.intent.name == "LoggerFreeToday"
