"""Tests for the numbers said in words."""

from __future__ import annotations

import pytest

from custom_components.logger_app import numbers, sentences


@pytest.mark.parametrize("lang", sentences.LANGUAGES)
def test_the_words_are_the_ones_assist_reads(lang: str) -> None:
    rbnf = pytest.importorskip("unicode_rbnf")
    engine = rbnf.RbnfEngine.for_language(lang)
    for n in range(1001):
        want = {
            w.replace("-", " ")
            for w in engine.format_number(n).text_by_ruleset.values()
        }
        assert set(numbers.said(lang, n)) == want, n


@pytest.mark.parametrize(
    ("lang", "n", "said"),
    [
        ("pt", 0, ["zero"]),
        ("pt", 2, ["dois", "duas"]),
        ("pt", 17, ["dezessete"]),
        ("pt", 100, ["cem"]),
        ("pt", 101, ["cento e um", "cento e uma"]),
        ("pt", 222, ["duzentos e vinte e dois", "duzentas e vinte e duas"]),
        ("pt", 1000, ["mil"]),
        ("en", 21, ["twenty one"]),
        ("en", 105, ["one hundred five"]),
        ("en", 1000, ["one thousand"]),
    ],
)
def test_said(lang: str, n: int, said: list[str]) -> None:
    assert numbers.said(lang, n) == said


def test_a_tenth_follows_every_number_but_the_last() -> None:
    tenths = "um|dois|três|quatro|cinco|seis|sete|oito|nove"
    assert numbers.rule("pt", {"from": 30, "to": 32, "fractions": "tenths"}) == (
        f"((trinta|trinta e um) [vírgula ({tenths})]|trinta e dois)"
    )
    assert numbers.rule("pt", {"from": 1, "to": 2}) == "(um|uma|dois|duas)"
    assert numbers.rule("en", {"from": 5, "to": 15, "step": 5}) == (
        "(five|ten|fifteen)"
    )


@pytest.mark.parametrize(
    ("value", "digits"),
    [(200, "200"), (72.4, "72.4"), (72.0, "72"), (0, "0"), ("x", None), (True, None)],
)
def test_digits(value: object, digits: str | None) -> None:
    assert numbers.digits(value) == digits


def test_only_up_to_a_thousand() -> None:
    with pytest.raises(ValueError, match="1001"):
        numbers.said("pt", 1001)
