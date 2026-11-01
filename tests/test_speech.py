"""What Assist says (speech.py): Logger's words in English, Portuguese built from its reading, the
answers from `/status` — over the contract's examples."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.logger_app import speech

from .conftest import CONTRACT

LISBON = ZoneInfo("Europe/Lisbon")
EAGER = CONTRACT["command_parse"]["eager"]
RAN = CONTRACT["command_run"]["201"]


@pytest.mark.parametrize(
    ("code", "lang"),
    [
        ("pt", "pt"),
        ("pt-PT", "pt"),
        ("pt_BR", "pt"),
        ("en", "en"),
        ("en-GB", "en"),
        (None, "en"),
        ("de", "en"),
    ],
)
def test_language(code: str | None, lang: str) -> None:
    assert speech.language(code) == lang


def test_done_english_is_loggers_words_portuguese_is_built() -> None:
    assert speech.done("en", EAGER, RAN) == "Logged Café 1 chávena"
    assert speech.done("pt", EAGER, RAN) == "Registado: Café 1 chávena."


def reading(
    intent: str, head: str, title: str, parts: list[str] | None = None
) -> dict[str, Any]:
    return {"intent": intent, "head": head, "title": title, "parts": parts or []}


@pytest.mark.parametrize(
    ("said", "answer", "words"),
    [
        (reading("food", "Plan", "Café 1 chávena"), {}, "Planeado: Café 1 chávena."),
        (
            reading("food", "Log", "Castanha de caju 40.5 g"),
            {},
            "Registado: Castanha de caju 40,5 g.",
        ),
        (reading("meds", "Took", "B12"), {}, "Tomado: B12."),
        (reading("meds", "Done", "Lentes"), {}, "Feito: Lentes."),
        (reading("shopping", "Buy", "Leite"), {}, "Na lista de compras: Leite."),
        (
            reading("shopping", "Buy", "Leite"),
            {"queued": True},
            "Na lista de compras, quando o Nextcloud responder: Leite.",
        ),
        (
            reading("task", "Add task", "ligar à mãe"),
            {},
            "Tarefa adicionada: ligar à mãe.",
        ),
        (reading("diary", "Diary", "dormi mal"), {}, "Nota guardada."),
        (reading("body", "Weight", "62.9 kg"), {}, "Peso registado: 62,9 quilos."),
        (
            reading("temperature", "Temperature", "38.2 °C"),
            {},
            "Temperatura registada: 38,2 graus.",
        ),
        (
            reading("pressure", "Pressure", "120/80", ["pulse 65", "now"]),
            {},
            "Tensão registada: 120 por 80, pulso 65.",
        ),
        (
            reading("symptom", "Check in", f"Tosse {speech.TIMES}1"),
            {},
            "Registado: Tosse 1.",
        ),
        (reading("money", "Spend €1.20", "Café"), {}, "Registado: Café, 1,20 euros."),
        (reading("water", "Drink", "Água"), {"words": "Drank Água"}, "Drank Água"),
    ],
)
def test_done_portuguese_per_intent(
    said: dict[str, Any], answer: dict[str, Any], words: str
) -> None:
    assert speech.done("pt", said, answer) == words


def test_nothing_written_words() -> None:
    needs = CONTRACT["command_parse"]["needs"]
    assert (
        speech.needs("pt", needs["needs"])
        == "Diz outra vez com a quantidade. Não registei nada."
    )
    assert (
        speech.needs("en", needs["needs"])
        == "Say it again with the amount. Nothing written."
    )
    warned = CONTRACT["command_parse"]["warning"]["warning"]
    assert speech.warning("en", warned) == f"{warned}. Nothing written."
    assert speech.unsure("pt", {"title": "banana com canela"}) == (
        "Não tenho a certeza: banana com canela? Não registei nada. Diz o nome completo."
    )
    assert (
        speech.unsure("en", {"title": ""})
        == "Not sure what to do with that. Nothing written."
    )
    assert speech.refused("pt", "Pick a list.") == "O Logger recusou: Pick a list."


@pytest.mark.parametrize(
    ("free", "pt", "en"),
    [
        (75, "Tens 75 calorias livres hoje.", "75 calories free today."),
        (-120, "Passaste 120 calorias hoje.", "120 calories over today."),
        (0, "Estás no objetivo de hoje.", "Right on today's goal."),
        (None, "Hoje não há objetivo de calorias.", "No calorie goal today."),
    ],
)
def test_free(free: int | None, pt: str, en: str) -> None:
    status = deepcopy(CONTRACT["status"])
    status["food"]["free"] = free
    assert (speech.free("pt", status), speech.free("en", status)) == (pt, en)
    assert speech.free("en", {}) == "No calorie goal today."


def test_due_from_status() -> None:
    status = CONTRACT["status"]
    assert speech.due("en", status, LISBON) == (
        "Overdue: Omega-3. Due now: Vitamin D and Stretch. Tasks: 1 overdue and 1 today."
    )
    assert speech.due("pt", status, LISBON) == (
        "Em atraso: Omega-3. Para agora: Vitamin D e Stretch. Tarefas: 1 em atraso e 1 para hoje."
    )


def test_due_nothing_owed_says_the_next_dose() -> None:
    status = deepcopy(CONTRACT["status"])
    for half in ("medication", "care"):
        status[half]["overdue"] = []
        status[half]["due"] = []
    status["tasks"] = None
    assert speech.due("pt", status, LISBON) == "A seguir: Magnesium às 21:00."
    status["medication"]["next"] = None
    assert speech.due("en", status, LISBON) == "Nothing due right now."


def test_join_says_how_many_more() -> None:
    names = ["A", "B", "C", "D", "E", "F"]
    assert speech.join("en", names) == "A, B, C, D and 2 more"
    assert speech.join("pt", ["A", "B"]) == "A e B"


def test_next_task() -> None:
    status = CONTRACT["status"]
    same_day = datetime(2026, 10, 25, 13, 30, tzinfo=UTC)
    assert speech.next_task("en", status, LISBON, now=same_day) == (
        "Next task: Call the clinic, at 11:30, overdue."
    )
    next_day = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    assert speech.next_task("pt", status, LISBON, now=next_day) == (
        "Próxima tarefa: Call the clinic, em atraso."
    )
    # A list said: its first task; the deliveries never count.
    assert speech.next_task("en", status, LISBON, "Home", now=same_day).startswith(
        "Next task: Call the clinic"
    )
    assert speech.next_task("pt", status, LISBON, "deliveries", now=same_day) == (
        "Não há tarefas para hoje na lista deliveries."
    )
    rows = [r for r in status["tasks"]["rows"] if not r.get("overdue")]
    later = {**status, "tasks": {**status["tasks"], "rows": rows}}
    assert speech.next_task("en", later, LISBON, now=same_day) == (
        "Next task: Water the plants, today."
    )
    assert speech.next_task("en", {**status, "tasks": None}, LISBON) == (
        "Tasks aren't connected in Logger."
    )
