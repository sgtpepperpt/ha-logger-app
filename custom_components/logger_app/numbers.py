"""Numbers in words, as Assist's number lists read them."""

from __future__ import annotations

from typing import Any

PT = "pt"
POINT = {PT: "vírgula", "en": "point"}

_PT_UNITS = (
    "zero",
    "um",
    "dois",
    "três",
    "quatro",
    "cinco",
    "seis",
    "sete",
    "oito",
    "nove",
)
_PT_TEENS = (
    "dez",
    "onze",
    "doze",
    "treze",
    "catorze",
    "quinze",
    "dezesseis",
    "dezessete",
    "dezoito",
    "dezenove",
)
_PT_TENS = (
    "",
    "",
    "vinte",
    "trinta",
    "quarenta",
    "cinquenta",
    "sessenta",
    "setenta",
    "oitenta",
    "noventa",
)
_PT_HUNDREDS = (
    "",
    "cento",
    "duzentos",
    "trezentos",
    "quatrocentos",
    "quinhentos",
    "seiscentos",
    "setecentos",
    "oitocentos",
    "novecentos",
)
_EN_UNITS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)
_EN_TEENS = (
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_EN_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def _pt_small(n: int, feminine: bool) -> str:
    if n < 10:
        return ("uma", "duas")[n - 1] if feminine and n in (1, 2) else _PT_UNITS[n]
    if n < 20:
        return _PT_TEENS[n - 10]
    tens, unit = divmod(n, 10)
    return _PT_TENS[tens] + (f" e {_pt_small(unit, feminine)}" if unit else "")


def _pt(n: int, feminine: bool) -> str:
    if n == 1000:
        return "mil"
    if n == 100:
        return "cem"
    hundreds, rest = divmod(n, 100)
    words: list[str] = []
    if hundreds:
        word = _PT_HUNDREDS[hundreds]
        words.append(f"{word[:-2]}as" if feminine and hundreds > 1 else word)
    if rest or not hundreds:
        words.append(_pt_small(rest, feminine))
    return " e ".join(words)


def _en_small(n: int) -> str:
    if n < 10:
        return _EN_UNITS[n]
    if n < 20:
        return _EN_TEENS[n - 10]
    tens, unit = divmod(n, 10)
    return _EN_TENS[tens] + (f" {_EN_UNITS[unit]}" if unit else "")


def _en(n: int) -> str:
    if n == 1000:
        return "one thousand"
    hundreds, rest = divmod(n, 100)
    words = [f"{_EN_UNITS[hundreds]} hundred"] if hundreds else []
    if rest or not hundreds:
        words.append(_en_small(rest))
    return " ".join(words)


def said(lang: str, n: int, feminine: bool = True) -> list[str]:
    if not 0 <= n <= 1000:
        raise ValueError(n)
    if lang != PT:
        return [_en(n)]
    return list(dict.fromkeys([_pt(n, False), *([_pt(n, True)] if feminine else [])]))


def rule(lang: str, spec: dict[str, Any]) -> str:
    values = list(range(spec["from"], spec["to"] + 1, spec.get("step", 1)))
    if spec.get("fractions") != "tenths":
        return "(" + "|".join(w for n in values for w in said(lang, n)) + ")"
    # A tenth is read in the masculine only ("setenta e dois vírgula um").
    whole = "|".join(w for n in values[:-1] for w in said(lang, n, feminine=False))
    tenth = "|".join(w for d in range(1, 10) for w in said(lang, d, feminine=False))
    last = "|".join(said(lang, values[-1], feminine=False))
    return f"(({whole}) [{POINT.get(lang, POINT['en'])} ({tenth})]|{last})"


def digits(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
