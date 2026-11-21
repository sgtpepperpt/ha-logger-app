"""Tests for the custom sentences."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml

from custom_components.logger_app import numbers, sentences
from custom_components.logger_app.voice import command_text

from .conftest import CONTRACT

VOCAB: dict[str, Any] = CONTRACT["command_vocab"]
CLASHING: dict[str, Any] = {
    "foods": [
        "Ovo",
        "ovos",
        "Pão de ló",
        "Pão-de-ló",
        "Iogurte",
        "IOGURTE",
        "Arroz doce",
        "arroz_doce",
        "Banana",
        "Chá verde",
        "Gengibre",
        "Maçãs",
        "Leite 2",
    ],
    "meals": ["Lanche", "lanche"],
    "usuals": ["Bananas", "Pão-de-ló", "Maçã", "Leite", "Detergente", "Detergentes"],
    "products": ["Gengibre", "Chá-verde", "Ovos", "Magnésio", "magnésio"],
    "units": ["fatias", "Fatia", "chávena"],
    "shops": ["Mercado", "mercado"],
    "lists": ["home <-> office", "home - office", "Casa"],
    "symptoms": ["Dor de cabeça", "dor-de-cabeça"],
    "money": {"items": ["Café", "café", "Café 2"], "presets": ["Renda", "renda"]},
}


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


# Speech-to-Phrase hears words only: case, hyphens and underscores tell no phrase apart.
def heard(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").replace("_", " ").split())


def phrases(lists: dict[str, dict[str, Any]], name: str) -> list[str]:
    out: list[str] = []
    for value in lists[name]["values"]:
        said = value if isinstance(value, str) else value["in"].strip("()")
        out.extend(heard(one) for one in said.split("|"))
    return out


@pytest.mark.parametrize("lang", sentences.LANGUAGES)
@pytest.mark.parametrize(
    "vocab", [VOCAB, CLASHING, {}], ids=["contract", "clashing", "empty"]
)
def test_each_list_says_each_phrase_once(vocab: dict[str, Any], lang: str) -> None:
    lists = sentences.lists(vocab, lang)
    for name, spec in lists.items():
        if "values" in spec:
            said = phrases(lists, name)
            twice = sorted({one for one in said if said.count(one) > 1})
            assert not twice, (name, twice)


@pytest.mark.parametrize("lang", sentences.LANGUAGES)
@pytest.mark.parametrize("vocab", [VOCAB, CLASHING], ids=["contract", "clashing"])
def test_a_food_is_never_also_a_shopping_item(vocab: dict[str, Any], lang: str) -> None:
    lists = sentences.lists(vocab, lang)
    assert not set(phrases(lists, "logger_food")) & set(phrases(lists, "logger_item"))


@pytest.mark.parametrize("vocab", [VOCAB, CLASHING], ids=["contract", "clashing"])
def test_in_portuguese_a_food_is_never_also_a_product(vocab: dict[str, Any]) -> None:
    lists = sentences.lists(vocab, "pt")
    assert not set(phrases(lists, "logger_food")) & set(
        phrases(lists, "logger_product")
    )


def test_names_said_alike_keep_the_first_spelling_and_the_food() -> None:
    pt = sentences.lists(CLASHING, "pt")
    assert pt["logger_food"]["values"] == [
        "Ovo",
        "ovos",
        "Pão de ló",
        {"in": "(Iogurte|Iogurtes)", "out": "Iogurte"},
        "Arroz doce",
        {"in": "(Banana|Bananas)", "out": "Banana"},
        "Chá verde",
        {"in": "(Gengibre|Gengibres)", "out": "Gengibre"},
        "Maçãs",
        {"in": "(Leite|Leites)", "out": "Leite"},
        {"in": "(Lanche|Lanches)", "out": "Lanche"},
    ]
    assert pt["logger_item"]["values"] == ["Detergente", "Detergentes"]
    assert pt["logger_product"]["values"] == ["Magnésio"]
    assert sentences.lists(CLASHING, "en")["logger_product"]["values"] == [
        "Gengibre",
        "Chá-verde",
        "Ovos",
        "Magnésio",
    ]
    assert pt["logger_symptom"]["values"] == ["Dor de cabeça"]
    assert pt["logger_list"]["values"] == ["home - office", "Casa"]
    assert pt["logger_money_item"]["values"] == ["Café"]
    units = pt["logger_unit"]["values"]
    assert {"chávena", "chávenas", "Fatia", "fatias"} <= {
        u for u in units if isinstance(u, str)
    }


# Speech-to-Phrase writes a range out at every use: one with tenths is used once.
@pytest.mark.parametrize("lang", sentences.LANGUAGES)
def test_a_range_with_tenths_is_used_once(lang: str) -> None:
    text = sentences.build(VOCAB, lang)
    for name, spec in sentences.RANGES.items():
        if "fractions" in spec:
            assert text.count(f"{{{name}}}") == 1, name


@pytest.mark.parametrize("lang", sentences.LANGUAGES)
def test_speech_to_phrase_s_copy_says_numbers_and_units_in_words(lang: str) -> None:
    text = sentences.build_stp(VOCAB, lang, "2026.11.0")
    assert sentences.is_ours(text)
    assert text.splitlines()[0] != sentences.build(VOCAB, lang).splitlines()[0]
    stp, full = yaml.safe_load(text), sentences.document(VOCAB, lang)
    said = {"logger_unit", *sentences.RANGES}
    assert set(stp["intents"]) == set(full["intents"])
    assert not set(stp["lists"]) & said
    assert said <= set(stp["expansion_rules"])
    every = [
        s
        for body in stp["intents"].values()
        for d in body["data"]
        for s in d["sentences"]
    ]
    for line in [*every, *stp["expansion_rules"].values()]:
        assert not {name for name in said if f"{{{name}}}" in line}, line
    after = "(de|do|da|dos|das)" if lang == "pt" else " of"
    assert stp["expansion_rules"]["logger_amount"].count(after) == 2


# Speech-to-Phrase hears what its copy says; Assist must read it with the full file's ranges.
@pytest.mark.parametrize("lang", sentences.LANGUAGES)
def test_assist_reads_every_number_speech_to_phrase_says(lang: str) -> None:
    hassil = pytest.importorskip("hassil")
    from hassil.parse_expression import parse_sentence
    from hassil.recognize import recognize_best
    from hassil.sample import sample_sentence

    rules = sentences.stp_document(VOCAB, lang)["expansion_rules"]
    value = {w: n for n in range(1001) for w in numbers.said(lang, n)}
    point = f" {numbers.POINT[lang]} "
    for name, spec in sentences.RANGES.items():
        found = hassil.Intents.from_dict(
            {
                "language": lang,
                "intents": {"N": {"data": [{"sentences": [f"n {{{name}}}"]}]}},
                "lists": {name: {"range": spec}},
            }
        )
        for phrase in sample_sentence(parse_sentence(rules[name])):
            words = " ".join(phrase.split())
            whole, _, tenth = words.partition(point)
            want = value[whole] + (value[tenth] / 10 if tenth else 0)
            result = recognize_best(f"n {words}", found)
            assert result is not None, (name, words)
            assert result.entities[name].value == pytest.approx(want), (name, words)


@pytest.mark.parametrize(
    ("said", "lang", "slots"),
    [
        (
            "comi duzentos gramas de Café ontem às treze horas",
            "pt",
            {"logger_grams": 200, "logger_food": "Café", "logger_hour": 13},
        ),
        ("regista duas chávenas de Café", "pt", {"logger_food": "Café"}),
        ("pesei setenta e dois vírgula quatro", "pt", {"logger_kg": 72.4}),
        (
            "a minha tensão é cento e dezenove por oitenta e pulso sessenta e cinco",
            "pt",
            {"logger_sys": 119, "logger_dia": 80, "logger_pulse": 65},
        ),
        (
            "gastei doze euros e cinquenta cêntimos em Café",
            "pt",
            {"logger_euros": 12, "logger_cents": 50, "logger_money_item": "Café"},
        ),
        ("contei trinta euros", "pt", {"logger_euros": 30}),
        (
            "i had two hundred grams of Café today at thirteen",
            "en",
            {"logger_grams": 200, "logger_food": "Café", "logger_hour": 13},
        ),
        ("my weight is seventy two point four", "en", {"logger_kg": 72.4}),
    ],
)
def test_both_files_read_what_speech_to_phrase_says_alike(
    said: str, lang: str, slots: dict[str, Any]
) -> None:
    hassil = pytest.importorskip("hassil")
    from hassil.recognize import recognize_best

    def read(text: str) -> dict[str, Any]:
        result = recognize_best(said, hassil.Intents.from_dict(yaml.safe_load(text)))
        assert result is not None, said
        return {
            name: round(e.value, 1) if isinstance(e.value, float) else e.value
            for name, e in result.entities.items()
            if name != "family"
        }

    assert read(sentences.build_stp(VOCAB, lang)).keys() >= {
        k for k in slots if k not in sentences.RANGES
    }
    got = read(sentences.build(VOCAB, lang))
    assert {k: got.get(k) for k in slots} == slots


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
    ("said", "lang", "unit"),
    [
        ("regista uma chávena de café", "pt", "chávena"),
        ("regista duas chávenas de café", "pt", "chávenas"),
        ("i had a slice of toast", "en", "slice"),
        ("i had two slices of toast", "en", "slices"),
    ],
)
def test_a_unit_is_the_word_said_and_the_line_is_sent_as_said(
    said: str, lang: str, unit: str
) -> None:
    result = best(said, lang)
    assert result is not None
    slots = {name: entity.value for name, entity in result.entities.items()}
    assert (slots.pop("family"), slots.pop("logger_unit")) == ("log", unit)
    assert command_text("log", said, slots, lang) == said


@pytest.mark.parametrize(
    ("said", "lang", "sent"),
    [
        ("o meu peso é 72,4", "pt", "peso 72.4"),
        ("peso 72,4 quilos", "pt", "peso 72.4"),
        ("pesei 72,4", "pt", "peso 72.4"),
        ("pesava 80 kg", "pt", "peso 80"),
        ("my weight is 72.4", "en", "weight 72.4"),
        ("weight 72.4 kilos", "en", "weight 72.4"),
        ("i weigh 72.4", "en", "weight 72.4"),
        ("weighed 80 kg", "en", "weight 80"),
    ],
)
def test_every_way_to_say_the_weight_reads_the_kilos(
    said: str, lang: str, sent: str
) -> None:
    result = best(said, lang)
    assert result is not None
    slots = {name: entity.value for name, entity in result.entities.items()}
    assert slots.pop("family") == "measure"
    assert "logger_kg" in slots
    assert command_text("measure", said, slots, lang) == sent


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
