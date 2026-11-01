"""What Assist says for Logger (F69), in the pipeline's language: Portuguese (pt-PT) or English.
PURE.

English is Logger's own words (its command core answers in English: `words`). Portuguese is
built here from the reading Logger gave (`GET /command/parse`: intent, head, title, parts), so a
pt-PT voice (Piper) never reads English: *Registado: Café, 1 chávena.* Logger's names stay as
Logger spells them. A refusal keeps Logger's sentence (it has no other), after a Portuguese lead.

Answers (`/status`, the contract's shape): `free` (food.free), `due` (the doses and care owed,
the tasks), `next_task` (the first task row). Logger's null is said as unknown, never as 0.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
import re
from typing import Any

PT, EN = "pt", "en"

WORDS: dict[str, dict[str, str]] = {
    "no_logger": {
        PT: "O Logger não está configurado no Home Assistant.",
        EN: "Logger isn't set up in Home Assistant.",
    },
    "no_voice": {
        PT: "Este Logger ainda não tem comandos de voz. Atualiza-o.",
        EN: "This Logger has no voice commands yet. Update it.",
    },
    "unreachable": {
        PT: "Não consigo falar com o Logger agora.",
        EN: "Logger can't be reached right now.",
    },
    "nothing_said": {PT: "Diz o que registar.", EN: "Say what to log."},
    "not_understood": {
        PT: "Não percebi. Não registei nada.",
        EN: "Not sure what to do with that. Nothing written.",
    },
    "unsure": {
        PT: "Não tenho a certeza: {guess}? Não registei nada. Diz o nome completo.",
        EN: "Not sure: {guess}? Nothing written. Say its full name.",
    },
    "needs": {
        PT: "Diz outra vez com {needs}. Não registei nada.",
        EN: "Say it again with {needs}. Nothing written.",
    },
    "warning": {PT: "{warning} Não registei nada.", EN: "{warning} Nothing written."},
    "refused": {PT: "O Logger recusou: {words}", EN: "{words}"},
    "free": {
        PT: "Tens {n} calorias livres hoje.",
        EN: "{n} calories free today.",
    },
    "over": {PT: "Passaste {n} calorias hoje.", EN: "{n} calories over today."},
    "on_goal": {
        PT: "Estás no objetivo de hoje.",
        EN: "Right on today's goal.",
    },
    "no_goal": {
        PT: "Hoje não há objetivo de calorias.",
        EN: "No calorie goal today.",
    },
    "overdue": {PT: "Em atraso: {names}.", EN: "Overdue: {names}."},
    "due_now": {PT: "Para agora: {names}.", EN: "Due now: {names}."},
    "next_dose": {PT: "A seguir: {name} às {at}.", EN: "Next: {name} at {at}."},
    "nothing_due": {PT: "Nada pendente agora.", EN: "Nothing due right now."},
    "tasks_count": {PT: "Tarefas: {parts}.", EN: "Tasks: {parts}."},
    "tasks_overdue_n": {PT: "{n} em atraso", EN: "{n} overdue"},
    "tasks_today_n": {PT: "{n} para hoje", EN: "{n} today"},
    "tasks_off": {
        PT: "As tarefas não estão ligadas no Logger.",
        EN: "Tasks aren't connected in Logger.",
    },
    "next_task": {
        PT: "Próxima tarefa: {summary}{when}.",
        EN: "Next task: {summary}{when}.",
    },
    "no_task": {
        PT: "Não há tarefas para hoje{where}.",
        EN: "No tasks due today{where}.",
    },
    "in_list": {PT: " na lista {name}", EN: " in {name}"},
    "late": {PT: ", em atraso", EN: ", overdue"},
    "today": {PT: ", hoje", EN: ", today"},
    "at": {PT: ", às {at}", EN: ", at {at}"},
    "and_more": {PT: "{names} e mais {n}", EN: "{names} and {n} more"},
}

# Logger's needs (command.py `need` names) as a sentence holds them.
NEEDS = {
    "amount": {PT: "a quantidade", EN: "the amount"},
    "food": {PT: "a comida", EN: "the food"},
    "item": {PT: "o que comprar", EN: "what to buy"},
    "level": {PT: "o nível", EN: "the level"},
    "title": {PT: "o título", EN: "the title"},
    "body": {PT: "o peso", EN: "the weight"},
    "temperature": {PT: "a temperatura", EN: "the temperature"},
    "reading": {PT: "a leitura", EN: "the reading"},
    "text": {PT: "a nota", EN: "the note"},
    "symptom": {PT: "o sintoma", EN: "the symptom"},
    "account": {PT: "a conta", EN: "the account"},
    "what": {PT: "o quê", EN: "what"},
}

MAX_NAMES = 4  # a list said out loud: the first few, then "and N more"
TIMES = "\u00d7"  # the times sign before a count in Logger's titles: not said
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def language(code: str | None) -> str:
    """`pt`, `pt-PT`, `pt_BR`… → pt; anything else → en."""
    return PT if (code or "").lower().replace("_", "-").split("-")[0] == PT else EN


def say(lang: str, key: str, **values: Any) -> str:
    return WORDS[key][lang].format(**values)


def _clean(text: str) -> str:
    """Logger's card words as a voice reads them: `·` as a pause, no curly quotes."""
    text = text.replace(" · ", ", ").replace("“", "").replace("”", "")
    return " ".join(text.split())


def number(lang: str, value: Any) -> str:
    """A number as said: no trailing zeros, Portugal's decimal comma."""
    text = f"{round(float(value), 2):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if lang == PT else text


def _numbers_pt(text: str) -> str:
    return _NUMBER.sub(lambda m: m.group(0).replace(".", ","), text)


def join(lang: str, names: list[str]) -> str:
    """`A, B and C` / `A, B e C`; past MAX_NAMES: the first ones and how many more."""
    names = [n for n in names if n]
    if len(names) > MAX_NAMES:
        return say(
            lang,
            "and_more",
            names=", ".join(names[:MAX_NAMES]),
            n=len(names) - MAX_NAMES,
        )
    if len(names) <= 1:
        return "".join(names)
    last = " e " if lang == PT else " and "
    return ", ".join(names[:-1]) + last + names[-1]


# ── the commands ─────────────────────────────────────────────────────────────


def _pt_done(reading: dict[str, Any], answer: dict[str, Any]) -> str | None:
    """Portuguese for a write Logger made, from its reading; None when the intent is new here."""
    intent, head = reading.get("intent"), str(reading.get("head") or "")
    title = _clean(str(reading.get("title") or ""))
    parts = [str(p) for p in reading.get("parts") or []]
    queued = answer.get("queued") is True
    if intent == "food":
        return f"{'Planeado' if head == 'Plan' else 'Registado'}: {_numbers_pt(title)}."
    if intent == "meds":
        return f"{'Feito' if head == 'Done' else 'Tomado'}: {title}."
    if intent == "shopping":
        wait = ", quando o Nextcloud responder" if queued else ""
        return f"Na lista de compras{wait}: {title}."
    if intent == "task":
        wait = ", quando o Nextcloud responder" if queued else ""
        return f"Tarefa adicionada{wait}: {title}."
    if intent == "diary":
        return "Nota guardada."
    if intent == "body" and (m := _NUMBER.search(title)):
        return f"Peso registado: {number(PT, m.group(0).replace(',', '.'))} quilos."
    if intent == "temperature" and (m := _NUMBER.search(title)):
        return (
            f"Temperatura registada: {number(PT, m.group(0).replace(',', '.'))} graus."
        )
    if intent == "pressure" and (m := re.match(r"(\d+)/(\d+)", title)):
        pulse = next((p.split()[-1] for p in parts if p.startswith("pulse ")), None)
        return (
            f"Tensão registada: {m.group(1)} por {m.group(2)}"
            + (f", pulso {pulse}" if pulse else "")
            + "."
        )
    if intent == "symptom":
        return f"Registado: {_numbers_pt(title.replace(TIMES, ''))}."
    if intent == "money":
        amount = re.search(r"€\s?(\d+(?:[.,]\d+)?)", head)
        euros = float(amount.group(1).replace(",", ".")) if amount else None
        said = f", {euros:.2f} euros".replace(".", ",") if euros is not None else ""
        return f"Registado: {title}{said}." if title else f"Registado{said}."
    return None


def done(lang: str, reading: dict[str, Any], answer: dict[str, Any]) -> str:
    """What was written: Logger's words (en), or Portuguese from the reading (pt)."""
    if lang == PT and (words := _pt_done(reading, answer)):
        return words
    words = answer.get("words") or reading.get("words") or ""
    return _clean(str(words)) or say(lang, "not_understood")


def needs(lang: str, missing: list[dict[str, Any]]) -> str:
    said = [
        NEEDS.get(str(n.get("name")), {}).get(lang) or str(n.get("label") or "").lower()
        for n in missing
        if isinstance(n, dict)
    ]
    return say(lang, "needs", needs=join(lang, [s for s in said if s]))


def unsure(lang: str, reading: dict[str, Any]) -> str:
    guess = _clean(str(reading.get("title") or ""))
    return say(lang, "unsure", guess=guess) if guess else say(lang, "not_understood")


def warning(lang: str, text: str) -> str:
    text = text.strip()
    return say(
        lang, "warning", warning=text if text.endswith((".", "!", "?")) else f"{text}."
    )


def refused(lang: str, words: str) -> str:
    return say(lang, "refused", words=_clean(words))


# ── the answers ──────────────────────────────────────────────────────────────


def _dig(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _int(value: Any) -> int | None:
    return (
        round(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else None
    )


def free(lang: str, status: dict[str, Any]) -> str:
    """`/status.food.free` said: free, over, on the goal — or no goal (null, never 0)."""
    n = _int(_dig(status, "food", "free"))
    if n is None:
        return say(lang, "no_goal")
    if n > 0:
        return say(lang, "free", n=n)
    if n < 0:
        return say(lang, "over", n=-n)
    return say(lang, "on_goal")


def _instant(value: Any, zone: tzinfo) -> datetime | None:
    """An instant (ISO, with its offset) on the caller's clock; None for a day or junk."""
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(zone) if moment.tzinfo is not None else None


def _clock(value: Any, zone: tzinfo) -> str | None:
    moment = _instant(value, zone)
    return moment.strftime("%H:%M") if moment else None


def _names(rows: Any) -> list[str]:
    return [
        str(r["name"])
        for r in rows or []
        if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"]
    ]


def due(lang: str, status: dict[str, Any], zone: tzinfo) -> str:
    """What is owed now (`/status` medication + care: overdue, due) — else the next dose — and
    the tasks overdue or due today."""
    said: list[str] = []
    overdue = _names(_dig(status, "medication", "overdue")) + _names(
        _dig(status, "care", "overdue")
    )
    due_now = _names(_dig(status, "medication", "due")) + _names(
        _dig(status, "care", "due")
    )
    if overdue:
        said.append(say(lang, "overdue", names=join(lang, overdue)))
    if due_now:
        said.append(say(lang, "due_now", names=join(lang, due_now)))
    if not overdue and not due_now:
        nxt = _dig(status, "medication", "next")
        at = (_dig(nxt, "slot", "at") if isinstance(nxt, dict) else None) or _clock(
            _dig(nxt, "due", "from"), zone
        )
        if isinstance(nxt, dict) and nxt.get("name") and at:
            said.append(say(lang, "next_dose", name=nxt["name"], at=at))
        else:
            said.append(say(lang, "nothing_due"))
    tasks = status.get("tasks") if isinstance(status.get("tasks"), dict) else None
    if tasks is not None:
        counts = [
            say(lang, key, n=n)
            for key, n in (
                ("tasks_overdue_n", _int(tasks.get("overdue"))),
                ("tasks_today_n", _int(tasks.get("today"))),
            )
            if n
        ]
        if counts:
            said.append(say(lang, "tasks_count", parts=join(lang, counts)))
    return " ".join(said)


def _fold(text: str) -> str:
    return " ".join(str(text).casefold().split())


def next_task(
    lang: str,
    status: dict[str, Any],
    zone: tzinfo,
    list_name: str | None = None,
    now: datetime | None = None,
) -> str:
    """The first task `/status.tasks.rows` holds (overdue first, then today's; deliveries aside),
    in `list_name` when one was said: its clock when it is today's, `overdue` when it is late."""
    tasks = status.get("tasks")
    if not isinstance(tasks, dict):
        return say(lang, "tasks_off")
    rows = [
        r
        for r in tasks.get("rows") or []
        if isinstance(r, dict)
        and r.get("kind", "task") == "task"
        and isinstance(r.get("summary"), str)
        and (list_name is None or _fold(r.get("list") or "") == _fold(list_name))
    ]
    where = say(lang, "in_list", name=list_name) if list_name else ""
    if not rows:
        return say(lang, "no_task", where=where)
    row = rows[0]
    today = (now or datetime.now(zone)).astimezone(zone).date()
    moment = _instant(row.get("due"), zone)
    if row.get("overdue") is True:
        when = (
            say(lang, "at", at=moment.strftime("%H:%M"))
            if moment and moment.date() == today
            else ""
        )
        when += say(lang, "late")
    else:
        when = (
            say(lang, "at", at=moment.strftime("%H:%M"))
            if moment
            else say(lang, "today")
        )
    return say(lang, "next_task", summary=row["summary"].strip(), when=when)
