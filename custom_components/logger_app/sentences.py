"""Assist custom sentences."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import re
from typing import Any

import yaml

LANGUAGES = ("pt", "en")
MARKER = "# Logger voice sentences"
INTENT_COMMAND = "LoggerCommand"
INTENT_FREE = "LoggerFreeToday"
INTENT_DUE = "LoggerWhatsDue"
INTENT_NEXT_TASK = "LoggerNextTask"
FAMILIES = ("log", "buy", "remind", "note", "feel", "measure", "spend")

FAMILY_INTENTS: dict[str, frozenset[str]] = {
    "log": frozenset(
        {"food", "meds", "body", "temperature", "pressure", "symptom", "money"}
    ),
    "buy": frozenset({"shopping"}),
    "remind": frozenset({"task"}),
    "note": frozenset({"diary"}),
    "feel": frozenset({"symptom"}),
    "measure": frozenset({"body", "temperature", "pressure"}),
    "spend": frozenset({"money"}),
}

MAX_WORDS = 8
MAX_CHARS = 60
_UNSAYABLE = re.compile(r"[^\w\s'\u2019-]|_")
_VOWEL_END = re.compile(r"(?<!ã)[aeiouáéíóúâêôà]$|ã$", re.IGNORECASE)

UNITS = {
    "pt": [
        "chávena",
        "chávenas",
        "caneca",
        "canecas",
        "copo",
        "copos",
        "fatia",
        "fatias",
        "colher",
        "colheres",
        "colherzinha",
        "colherzinhas",
        "unidade",
        "unidades",
        "peça",
        "peças",
        "lata",
        "latas",
        "garrafa",
        "garrafas",
        "pacote",
        "pacotes",
        "embalagem",
        "embalagens",
        "tigela",
        "tigelas",
        "taça",
        "taças",
        "porção",
        "porções",
        "dose",
        "doses",
        "medida",
        "medidas",
        "barra",
        "barras",
    ],
    "en": [
        "cup",
        "cups",
        "mug",
        "mugs",
        "glass",
        "glasses",
        "slice",
        "slices",
        "spoon",
        "spoons",
        "tablespoon",
        "tablespoons",
        "teaspoon",
        "teaspoons",
        "piece",
        "pieces",
        "unit",
        "units",
        "can",
        "cans",
        "bottle",
        "bottles",
        "pack",
        "packs",
        "packet",
        "packets",
        "bowl",
        "bowls",
        "portion",
        "portions",
        "serving",
        "servings",
        "scoop",
        "scoops",
        "bar",
        "bars",
    ],
}
SAID_AS = {
    "un": "unidade",
    "und": "unidade",
    "unid": "unidade",
    "pc": "piece",
    "pcs": "piece",
}
MEASURES = {
    "pt": [
        {"in": "(peso|pesei|pesava)", "out": "peso"},
        {"in": "(temperatura|febre)", "out": "temperatura"},
        {"in": "(tensão|pressão arterial|pressão)", "out": "tensão"},
    ],
    "en": [
        {"in": "(weight|weigh|weighed|i weigh|i weighed)", "out": "weight"},
        {"in": "(temperature|fever)", "out": "temperature"},
        {"in": "(blood pressure|pressure|bp)", "out": "blood pressure"},
    ],
}
_TRAILING_NUMBERS = re.compile(r"(?:\s+\S*\d\S*)+$")

TEMPLATES: dict[str, dict[str, Any]] = {
    "pt": {
        "rules": {
            "logger_amount": (
                "((um|uma|meio|meia|dois|duas|três|quatro|{logger_count}) "
                "[{logger_unit} [de|do|da|dos|das]]"
                "|{logger_grams} (g|grama|gramas|ml|mililitros) [de|do|da|dos|das])"
            ),
            "logger_when": ("(hoje|ontem|agora|[hoje|ontem] às {logger_hour} [horas])"),
            "logger_by": (
                "(hoje|amanhã|depois de amanhã|[na|no|até] (segunda|terça|quarta|quinta|"
                "sexta|sábado|domingo))"
            ),
            "logger_article": "(o|a|os|as|um|uma)",
            "logger_counted": "(contei|contei dinheiro|contei o dinheiro|dinheiro contado)",
        },
        "command": {
            "log": [
                "(regista|registar|registei|comi|bebi|almocei|jantei|lanchei|tomei|já tomei"
                "|fiz|já fiz) {logger_text}",
                "(regista|registar|comi|bebi|almocei|jantei|lanchei|tomei) [<logger_amount>]"
                " {logger_food} [<logger_when>]",
                "(tomei|já tomei) [o|a|os|as] {logger_product} [<logger_when>]",
                "(fiz|já fiz) [o|a|os|as] {logger_product}",
            ],
            "buy": [
                "(comprar|compra|preciso de comprar|temos de comprar|falta|faltam)"
                " {logger_text}",
                "(comprar|compra|preciso de comprar|falta|faltam) [<logger_amount>]"
                " ({logger_item}|{logger_food}) [(no|na|em) {logger_shop}] [<logger_by>]",
            ],
            "remind": [
                "(lembra-me de|lembra me de|lembrar-me de|não esquecer de|tarefa)"
                " {logger_text}",
            ],
            "note": ["(nota|anota|diário) {logger_text}"],
            "feel": [
                "(sinto|senti|sinto-me|estou com|ando com) {logger_text}",
                "(sinto|senti|tenho|tive|estou com|ando com) [<logger_article>]"
                " {logger_symptom} [[nível|grau] {logger_level}]",
            ],
            "measure": [
                "[o|a] [meu|minha] {logger_measure} {logger_text}",
                "[o] [meu] peso [é|está|foi|de] {logger_kg} [quilos|kg]",
                "(pesei|pesava) {logger_kg} [quilos|kg]",
                "[a] [minha] (temperatura|febre) [é|está|foi|de] {logger_celsius} [graus]",
                "[a] [minha] (tensão|pressão arterial) [é|está|foi|de] {logger_sys}"
                " (por|sobre) {logger_dia} [[e] (pulso|pulsação) {logger_pulse}]",
            ],
            "spend": [
                "(gastei|paguei) {logger_text}",
                "(gastei|paguei) {logger_euros} [euro|euros] [e {logger_cents} [cêntimo|cêntimos]]"
                " (em|no|na|com) {logger_money_item}",
                "paguei [a|o] {logger_preset}",
                "<logger_counted>",
                "<logger_counted> {logger_euros} [euro|euros] [e {logger_cents} [cêntimo|cêntimos]]",
                "<logger_counted> {logger_text}",
            ],
        },
        "free": [
            "quanto (tenho livre|me resta|me falta) [hoje]",
            "quantas calorias (tenho livres|me restam|tenho) [hoje]",
            "quanto posso [ainda] comer [hoje]",
        ],
        "due": [
            "o que (falta|tenho para tomar|tenho pendente|está pendente|tenho de fazer)"
            " [hoje|agora]",
            "o que há para hoje",
        ],
        "next_task": [
            "(qual é a próxima|próxima) tarefa [(da|na|em) [lista] {logger_list}]",
            "o que tenho a seguir [(da|na|em) [lista] {logger_list}]",
        ],
    },
    "en": {
        "rules": {
            "logger_amount": (
                "((a|an|one|half a|two|three|four|{logger_count}) "
                "[{logger_unit} [of]]"
                "|{logger_grams} (g|gram|grams|ml|millilitres|milliliters) [of])"
            ),
            "logger_when": (
                "(today|yesterday|now|[today|yesterday] at {logger_hour}"
                " [o'clock|am|pm])"
            ),
            "logger_by": (
                "(today|tomorrow|[by|on] (monday|tuesday|wednesday|thursday|friday"
                "|saturday|sunday))"
            ),
            "logger_article": "(a|an|the|my)",
            "logger_counted": (
                "(i counted|i counted cash|counted cash|count cash|cash count|cash counted)"
            ),
        },
        "command": {
            "log": [
                "(log|logged|i ate|ate|i had|had|i drank|drank|i took|took|i did|did)"
                " {logger_text}",
                "(log|i ate|ate|i had|had|i drank|drank) [<logger_amount>]"
                " {logger_food} [<logger_when>]",
                "[i] took [the|my] {logger_product} [<logger_when>]",
                "[i] did [the|my] {logger_product}",
            ],
            "buy": [
                "(buy|we need|need to buy|i need to buy) {logger_text}",
                "(buy|we need|need to buy) [<logger_amount>]"
                " ({logger_item}|{logger_food}) [at {logger_shop}] [<logger_by>]",
            ],
            "remind": [
                "(remind me to|remember to|don't forget to|task) {logger_text}",
            ],
            "note": ["(note|diary|journal) {logger_text}"],
            "feel": [
                "(i feel|i felt|felt|feeling|i'm feeling) {logger_text}",
                "(i feel|i felt|i have) [<logger_article>] {logger_symptom}"
                " [[level] {logger_level}]",
            ],
            "measure": [
                "[my] {logger_measure} {logger_text}",
                "[my] weight [is] {logger_kg} [kilos|kg]",
                "[i] (weigh|weighed) {logger_kg} [kilos|kg]",
                "[my] (temperature|fever) [is] {logger_celsius} [degrees]",
                "[my] (blood pressure|bp) [is] {logger_sys} over {logger_dia}"
                " [[and] pulse {logger_pulse}]",
            ],
            "spend": [
                "(i spent|spent|i paid|paid) {logger_text}",
                "[i] (spent|paid) {logger_euros} [euro|euros] [and {logger_cents} [cent|cents]]"
                " (on|at|for) {logger_money_item}",
                "[i] paid [the] {logger_preset}",
                "<logger_counted>",
                "<logger_counted> {logger_euros} [euro|euros] [and {logger_cents} [cent|cents]]",
                "<logger_counted> {logger_text}",
            ],
        },
        "free": [
            "how much is (free|left) [today]",
            "how many (calories|kcal) [do i have] (left|free) [today]",
            "how much can i [still] eat [today]",
        ],
        "due": [
            "what's (due|pending) [today|now]",
            "what is (due|pending) [today|now]",
            "what do i have to take [today|now]",
        ],
        "next_task": [
            "what's [my] next task [(in|on) [the] {logger_list} [list]]",
            "what is [my] next task [(in|on) [the] {logger_list} [list]]",
            "next task [(in|on) [the] {logger_list} [list]]",
        ],
    },
}

RANGES: dict[str, dict[str, Any]] = {
    "logger_count": {"from": 5, "to": 20},
    "logger_grams": {"from": 5, "to": 1000, "step": 5},
    "logger_hour": {"from": 0, "to": 23},
    "logger_kg": {"from": 30, "to": 200, "fractions": "tenths"},
    "logger_celsius": {"from": 34, "to": 42, "fractions": "tenths"},
    "logger_sys": {"from": 70, "to": 230},
    "logger_dia": {"from": 40, "to": 140},
    "logger_pulse": {"from": 35, "to": 200},
    "logger_level": {"from": 0, "to": 10},
    "logger_euros": {"from": 0, "to": 500},
    "logger_cents": {"from": 1, "to": 99},
}


def spoken(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    text = " ".join(_UNSAYABLE.sub(" ", name).split()).strip(" -'\u2019")
    if not re.search(r"[^\W\d_]", text):
        return None
    if len(text) > MAX_CHARS or len(text.split()) > MAX_WORDS:
        return None
    return text


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def _values(
    names: Iterable[Any], plural: bool = False, trim: bool = False
) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for name in names:
        said = spoken(name)
        if said is not None and trim:
            said = spoken(_TRAILING_NUMBERS.sub("", said)) or said
        if said is None or _fold(said) in seen:
            continue
        seen.add(_fold(said))
        if plural and " " not in said and _VOWEL_END.search(said):
            out.append({"in": f"({said}|{said}s)", "out": said})
        else:
            out.append(said)
    return out


def _units(lang: str, labels: Iterable[Any]) -> list[Any]:
    words: list[Any] = []
    for label in labels:
        said = spoken(label)
        if said is None:
            continue
        said = SAID_AS.get(_fold(said), said)
        words.append(said)
    return _values([*words, *UNITS[lang]], plural=True)


def lists(vocab: dict[str, Any], lang: str) -> dict[str, dict[str, Any]]:
    def got(key: str) -> list[Any]:
        value = vocab.get(key)
        return value if isinstance(value, list) else []

    money = vocab.get("money") if isinstance(vocab.get("money"), dict) else {}

    def money_got(key: str) -> list[Any]:
        value = money.get(key)
        return value if isinstance(value, list) else []

    foods = _values([*got("foods"), *got("meals")], plural=True, trim=True)
    named = {_fold(v if isinstance(v, str) else v["out"]) for v in foods}
    items = [
        v
        for v in _values(got("usuals"), plural=True, trim=True)
        if _fold(v if isinstance(v, str) else v["out"]) not in named
    ]
    out: dict[str, dict[str, Any]] = {
        "logger_text": {"wildcard": True},
        "logger_measure": {"values": [dict(m) for m in MEASURES[lang]]},
    }
    for name, values in (
        ("logger_food", foods),
        ("logger_product", _values(got("products"))),
        ("logger_item", items),
        ("logger_shop", _values(got("shops"))),
        ("logger_list", _values(got("lists"))),
        ("logger_symptom", _values(got("symptoms"))),
        ("logger_unit", _units(lang, got("units"))),
        ("logger_money_item", _values(money_got("items"), trim=True)),
        ("logger_preset", _values(money_got("presets"))),
    ):
        out[name] = {"values": values}
    for name, spec in RANGES.items():
        out[name] = {"range": dict(spec)}
    return out


def document(vocab: dict[str, Any], lang: str) -> dict[str, Any]:
    spec = TEMPLATES[lang]
    rules: dict[str, str] = spec["rules"]
    intents: dict[str, Any] = {
        INTENT_COMMAND: {
            "data": [
                {
                    "sentences": list(spec["command"][family]),
                    "slots": {"family": family},
                }
                for family in FAMILIES
            ]
        }
    }
    for intent, key in (
        (INTENT_FREE, "free"),
        (INTENT_DUE, "due"),
        (INTENT_NEXT_TASK, "next_task"),
    ):
        intents[intent] = {"data": [{"sentences": list(spec[key])}]}
    every = [s for i in intents.values() for d in i["data"] for s in d["sentences"]]
    used_rules = {
        name: rule
        for name, rule in rules.items()
        if any(f"<{name}>" in s for s in every)
    }
    used = {
        name
        for text in [*every, *used_rules.values()]
        for name in re.findall(r"\{(\w+)", text)
    }
    return {
        "language": lang,
        "intents": intents,
        "lists": {
            name: spec_ for name, spec_ in lists(vocab, lang).items() if name in used
        },
        "expansion_rules": used_rules,
    }


def build(vocab: dict[str, Any], lang: str, version: str = "") -> str:
    body = yaml.safe_dump(
        document(vocab, lang), allow_unicode=True, sort_keys=False, width=100
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    head = (
        f"{MARKER} · {lang} · {digest}\n"
        f"# Written by the Logger integration{f' {version}' if version else ''} from Logger's"
        " words; rewritten when they change. Edits here are overwritten.\n"
    )
    return head + body


def is_ours(text: str) -> bool:
    return text.startswith(MARKER)


def counts(vocab: dict[str, Any], lang: str = "pt") -> dict[str, int]:
    return {
        name.removeprefix("logger_"): len(spec["values"])
        for name, spec in lists(vocab, lang).items()
        if "values" in spec
    }
