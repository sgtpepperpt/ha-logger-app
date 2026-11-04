"""Tests for the custom sentences."""

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
        ("a b c d e f g h i", None),
    ],
)
def test_spoken(name: Any, said: str | None) -> None:
    assert sentences.spoken(name) == said


def test_lists_from_the_vocabulary() -> None:
    lists = sentences.lists(VOCAB, "pt")
    foods = lists["logger_food"]["values"]
    assert {"in": "(Café|Cafés)", "out": "Café"} in foods
    assert "Toast" in foods
    assert "Breakfast" in foods
    assert lists["logger_item"]["values"] == [
        {"in": "(Leite|Leites)", "out": "Leite"},
        "Pão",
        {"in": "(Detergente|Detergentes)", "out": "Detergente"},
    ]
    units = [
        u if isinstance(u, str) else u["out"] for u in lists["logger_unit"]["values"]
    ]
    assert units[:6] == [
        "chávena",
        "cup",
        "glass",
        "mug",
        "slice",
        "unidade",
    ]
    assert "colheres" in units
    assert lists["logger_list"]["values"][2] == "home - office"
    assert lists["logger_text"] == {"wildcard": True}
    assert lists["logger_kg"]["range"]["fractions"] == "tenths"
    dated = sentences.lists(
        {
            "foods": ["Cooked chickpeas (16/8/2026)", "Dark chocolate 85%"],
            "products": ["Magnesium"],
        },
        "pt",
    )
    assert dated["logger_food"]["values"] == ["Cooked chickpeas", "Dark chocolate"]
    assert dated["logger_product"]["values"] == ["Magnesium"]
    assert sentences.counts(VOCAB)["food"] == len(foods)


def test_nothing_from_logger_still_makes_a_file() -> None:
    doc = yaml.safe_load(sentences.build({}, "en"))
    assert doc["lists"]["logger_food"] == {"values": []}
    assert doc["lists"]["logger_unit"]["values"]


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
    ("said", "lang", "sent"),
    [
        ("contei 30 euros", "pt", "contei 30,00 €"),
        ("contei o dinheiro 12 euros e 50 cêntimos", "pt", "contei 12,50 €"),
        ("dinheiro contado 0", "pt", "contei 0,00 €"),
        ("contei 1250,40 euros", "pt", "contei 1250,40 euros"),
        ("contei", "pt", "contei"),
        ("contei uma história", "pt", "contei uma história"),
        ("i counted 30 euros", "en", "counted cash 30,00 €"),
        ("count cash 7", "en", "counted cash 7,00 €"),
        ("i counted 30.50 euros", "en", "counted cash 30.50 euros"),
        ("counted cash", "en", "counted cash"),
        ("i counted the steps", "en", "i counted the steps"),
    ],
)
def test_the_cash_counted_is_a_spend_led_as_logger_reads_it(
    said: str, lang: str, sent: str
) -> None:
    result = best(said, lang)
    assert result is not None
    slots = {name: entity.value for name, entity in result.entities.items()}
    assert slots.pop("family") == "spend"
    assert command_text("spend", said, slots, lang) == sent


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
        ),
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
