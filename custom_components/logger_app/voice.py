"""Voice (F69): Assist's commands and answers through Logger, and the sentences they need.

**Intents** (registered once, `async_setup`; any Assist pipeline, any satellite):

- `LoggerCommand` (sentences.py: the families `log` `buy` `remind` `note` `feel` `measure`
  `spend`): the words heard go to Logger's command core whole. `GET /command/parse` reads them
  (nothing written); the reading kept is the first — Logger's best, then its alternatives — of
  a kind the family allows (sentences.FAMILY_INTENTS: a `buy` sentence is never written as a
  task). Only an *eager* one runs (Logger is sure and more than a bare name was said: what the
  palette's Enter takes), `POST /command/run {text, intent, source: voice}`; anything less is
  said back with nothing written: what is missing, Logger's warning, its guess. The few things
  a voice can't say are put back first (`command_text`): the `tarefa:` / `nota:` markers'
  colon, `120/80`, a measure's numbers from the sentence's slots, an amount in euros.
- `LoggerFreeToday`, `LoggerWhatsDue`, `LoggerNextTask`: a fresh `GET /status`, said.

The words are the pipeline's language (speech.py). A write lands in Logger's Audit tab as
client *ha*, source *voice*, and fires `logger_app_fired {kind: command, name: <intent>,
words}`. An LLM agent with the Assist API sees the same intents as tools (`text` + `family`).

**Sentences** (`VoiceSentences`, the entry's voice option, off by default): sentences.py's file
for pt and en, written where Home Assistant's agent reads custom sentences and — when /share
exists (Home Assistant OS) — in the Speech-to-Phrase add-on's folder there too. Built from
`GET /command/vocab` once Home Assistant has started, every VOICE_EVERY_H and on Refresh; a
file is written only when its bytes change (Speech-to-Phrase then learns again on its next
command) and Home Assistant's agent is reloaded (`conversation.reload`). Only files this
integration wrote are replaced or removed (sentences.MARKER): the option off removes them, as
does removing the entry.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import sentences, speech
from .api import LoggerError, LoggerRefused
from .const import (
    CONF_VOICE,
    DEFAULT_VOICE,
    DOMAIN,
    FEATURE_VOICE,
    SENTENCES_DIR,
    SENTENCES_FILE,
    SHARE_DIR,
    SPEECH_TO_PHRASE_DIR,
    VOICE_EVERY_H,
)

if TYPE_CHECKING:
    from .coordinator import LoggerConfigEntry, LoggerCoordinator

_LOGGER = logging.getLogger(__name__)

# An LLM's call names what to tell Logger (`text`); a sentence's words are its text input.
SLOT_TEXT = "text"
SLOT_FAMILY = "family"

# The words Logger reads for a measure said with its numbers (command.py WEIGHT_WORDS …).
_MEASURE_WORDS = {
    speech.PT: {
        "kg": "peso",
        "celsius": "temperatura",
        "bp": "tensão",
        "pulse": "pulso",
    },
    speech.EN: {
        "kg": "weight",
        "celsius": "temperature",
        "bp": "blood pressure",
        "pulse": "pulse",
    },
}
# A marker Logger reads only with its colon (command.py TASK_MARKERS, DIARY_MARKERS): said
# without one, it gets it back. `anota` is said, `nota` is read.
_MARKERS = {
    "remind": {"tarefa": "tarefa", "task": "task", "todo": "todo"},
    "note": {
        "nota": "nota",
        "anota": "nota",
        "diário": "diário",
        "diario": "diário",
        "note": "note",
        "diary": "diary",
        "journal": "journal",
    },
}
# `120 por 80`, `120 over 80`: a pressure as Logger reads it, `120/80`.
_OVER = re.compile(
    r"\b(\d{2,3})\s+(?:por|sobre|over|by|barra|slash)\s+(\d{2,3})\b", re.IGNORECASE
)


# ── which Logger ─────────────────────────────────────────────────────────────


def voice_logger(hass: HomeAssistant) -> tuple[LoggerCoordinator | None, str | None]:
    """The loaded Logger that answers voice — the one whose sentences are on, else the first
    offering voice — or (None, why): `no_logger`, `no_voice` (a Logger older than F69)."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return None, "no_logger"
    voiced = [e for e in entries if FEATURE_VOICE in e.runtime_data.about.features]
    if not voiced:
        return None, "no_voice"
    chosen = [e for e in voiced if e.options.get(CONF_VOICE, DEFAULT_VOICE)] or voiced
    return chosen[0].runtime_data, None


def _slot(intent_obj: intent.Intent, name: str) -> Any:
    slot = intent_obj.slots.get(name)
    return slot.get("value") if isinstance(slot, dict) else None


# ── a command ────────────────────────────────────────────────────────────────


def command_text(
    family: str | None, said: str, slots: dict[str, Any], lang: str
) -> str:
    """The line Logger reads for what was said: the words as heard, with what a voice can't
    say put back (module docstring)."""
    text = " ".join(str(said or "").split()).rstrip(".!?…").strip()
    words = _MEASURE_WORDS[lang]
    if family == "measure":
        sys_, dia = slots.get("logger_sys"), slots.get("logger_dia")
        if sys_ is not None and dia is not None:
            pulse = slots.get("logger_pulse")
            return f"{words['bp']} {int(sys_)}/{int(dia)}" + (
                f" {words['pulse']} {int(pulse)}" if pulse is not None else ""
            )
        for key in ("kg", "celsius"):
            if (value := slots.get(f"logger_{key}")) is not None:
                return f"{words[key]} {speech.number(speech.EN, value)}"
        measure, rest = slots.get("logger_measure"), slots.get("logger_text")
        if measure and rest:
            text = f"{measure} {rest}"
        return _OVER.sub(r"\1/\2", text)
    if family == "spend":
        euros, item = slots.get("logger_euros"), slots.get("logger_money_item")
        if euros is not None and item:
            amount = f"{int(euros)},{int(slots.get('logger_cents') or 0):02d} €"
            return (
                f"gastei {amount} em {item}"
                if lang == speech.PT
                else f"spent {amount} on {item}"
            )
        return text
    if family in _MARKERS:
        first, _, rest = text.partition(" ")
        marker = _MARKERS[family].get(first.casefold().rstrip(":,"))
        if marker and rest.strip():
            return f"{marker}: {rest.strip()}"
    return text


def choose(
    reading: dict[str, Any], family: str | None
) -> tuple[dict[str, Any] | None, str]:
    """(the reading to act on, '') — or (the one to talk about, why not): `none` (nothing of the
    family's kind), `needs`, `warning`, `unsure` (Logger isn't sure: no write without it)."""
    allowed = sentences.FAMILY_INTENTS.get(family) if family else None
    readings = [reading, *(reading.get("alternatives") or [])]
    kin = [
        r
        for r in readings
        if isinstance(r, dict)
        and isinstance(r.get("intent"), str)
        and (allowed is None or r["intent"] in allowed)
    ]
    if not kin:
        return None, "none"
    if sure := next((r for r in kin if r.get("eager") is True), None):
        return sure, ""
    first = kin[0]
    if first.get("needs"):
        return first, "needs"
    if first.get("warning"):
        return first, "warning"
    return first, "unsure"


def _not_written(lang: str, reading: dict[str, Any] | None, why: str) -> str:
    if reading is None or why == "none":
        return speech.say(lang, "not_understood")
    if why == "needs":
        return speech.needs(lang, reading.get("needs") or [])
    if why == "warning":
        return speech.warning(lang, str(reading.get("warning")))
    return speech.unsure(lang, reading)


def _failed(response: intent.IntentResponse, words: str) -> intent.IntentResponse:
    """Nothing was done: an error response that says why (Assist speaks it)."""
    response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, words)
    return response


class LoggerCommandIntent(intent.IntentHandler):
    """`LoggerCommand`: a sentence of a family (module docstring)."""

    intent_type = sentences.INTENT_COMMAND
    description = (
        "Tells Logger, the health logger, something to do: log a food, a dose or a "
        "reading, buy something, remind about something, write a note"
    )

    @property
    def slot_schema(self) -> dict | None:
        return {
            vol.Optional(SLOT_TEXT): cv.string,
            vol.Optional(SLOT_FAMILY): vol.In(sentences.FAMILIES),
        }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        lang = speech.language(intent_obj.language)
        response = intent_obj.create_response()
        coordinator, why = voice_logger(intent_obj.hass)
        if coordinator is None:
            return _failed(response, speech.say(lang, why or "no_logger"))
        family = _slot(intent_obj, SLOT_FAMILY)
        slots = {
            name: _slot(intent_obj, name)
            for name in intent_obj.slots
            if name.startswith("logger_")
        }
        said = _slot(intent_obj, SLOT_TEXT) or intent_obj.text_input or ""
        text = command_text(family, said, slots, lang)
        if not text:
            return _failed(response, speech.say(lang, "nothing_said"))
        client = coordinator.client
        try:
            reading = await client.command_parse(text)
        except LoggerRefused as err:
            return _failed(response, speech.refused(lang, err.words))
        except LoggerError:
            return _failed(response, speech.say(lang, "unreachable"))
        kept, why = choose(reading, family)
        if why:
            return _failed(response, _not_written(lang, kept, why))
        assert kept is not None
        try:
            answer = await client.command_run(text, kept["intent"])
        except LoggerRefused as err:
            needs = err.body.get("needs") if isinstance(err.body, dict) else None
            words = (
                speech.needs(lang, needs) if needs else speech.refused(lang, err.words)
            )
            return _failed(response, words)
        except LoggerError:
            return _failed(response, speech.say(lang, "unreachable"))
        response.async_set_speech(speech.done(lang, kept, answer))
        await coordinator.async_voice_fired(answer)
        return response


class _AnswerIntent(intent.IntentHandler):
    """An answer read from a fresh `/status` (speech.py says it)."""

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        lang = speech.language(intent_obj.language)
        response = intent_obj.create_response()
        coordinator, why = voice_logger(intent_obj.hass)
        if coordinator is None:
            return _failed(response, speech.say(lang, why or "no_logger"))
        try:
            status = await coordinator.client.status()
        except LoggerError:
            return _failed(response, speech.say(lang, "unreachable"))
        zone = (
            dt_util.get_time_zone(coordinator.client.tz)
            or dt_util.get_default_time_zone()
        )
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(self.answer(lang, status, zone, intent_obj))
        return response

    def answer(
        self, lang: str, status: dict[str, Any], zone: Any, intent_obj: intent.Intent
    ) -> str:
        raise NotImplementedError


class LoggerFreeTodayIntent(_AnswerIntent):
    intent_type = sentences.INTENT_FREE
    description = "How many kcal are still free today in Logger, the health logger"

    def answer(self, lang, status, zone, intent_obj):
        return speech.free(lang, status)


class LoggerWhatsDueIntent(_AnswerIntent):
    intent_type = sentences.INTENT_DUE
    description = "What is due now in Logger: doses, care, tasks"

    def answer(self, lang, status, zone, intent_obj):
        return speech.due(lang, status, zone)


class LoggerNextTaskIntent(_AnswerIntent):
    intent_type = sentences.INTENT_NEXT_TASK
    description = "The next task due today in Logger, optionally in one list"

    @property
    def slot_schema(self) -> dict | None:
        return {vol.Optional("logger_list"): cv.string}

    def answer(self, lang, status, zone, intent_obj):
        return speech.next_task(lang, status, zone, _slot(intent_obj, "logger_list"))


@callback
def async_setup_intents(hass: HomeAssistant) -> None:
    for handler in (
        LoggerCommandIntent(),
        LoggerFreeTodayIntent(),
        LoggerWhatsDueIntent(),
        LoggerNextTaskIntent(),
    ):
        intent.async_register(hass, handler)


# ── the sentences file ───────────────────────────────────────────────────────


# Speech-to-Phrase looks for a language's folder by its model's language (`pt_PT`) and then by
# the language's family (`pt`): both get the file. Home Assistant's agent: the family's only.
STP_FOLDERS = {"pt": ("pt", "pt_PT"), "en": ("en",)}


def _paths(config_dir: Path, lang: str) -> list[Path]:
    """Where `lang`'s file goes: Home Assistant's custom sentences, and Speech-to-Phrase's
    folder when /share is there (Home Assistant OS). Blocking: run in the executor."""
    paths = [config_dir / lang / SENTENCES_FILE]
    if Path(SHARE_DIR).is_dir():
        paths += [
            Path(SPEECH_TO_PHRASE_DIR) / folder / SENTENCES_FILE
            for folder in STP_FOLDERS.get(lang, (lang,))
        ]
    return paths


def write_files(config_dir: Path, files: dict[str, str]) -> dict[str, list[str]]:
    """`files` (language → text) everywhere it is read: written when new or changed; a file
    that isn't this integration's is kept as it is. Blocking."""
    done: dict[str, list[str]] = {"written": [], "unchanged": [], "kept": []}
    for lang, text in files.items():
        for path in _paths(config_dir, lang):
            if path.exists():
                old = path.read_text(encoding="utf-8", errors="replace")
                if not sentences.is_ours(old):
                    done["kept"].append(str(path))
                    continue
                if old == text:
                    done["unchanged"].append(str(path))
                    continue
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_name(f".{SENTENCES_FILE}.part")  # never read: not *.yaml
            part.write_text(text, encoding="utf-8")
            part.replace(path)
            done["written"].append(str(path))
    return done


def remove_files(config_dir: Path) -> dict[str, list[str]]:
    """This integration's files, removed wherever they are (another's kept). Blocking."""
    done: dict[str, list[str]] = {"removed": [], "kept": []}
    for lang in sentences.LANGUAGES:
        for path in _paths(config_dir, lang):
            if not path.exists():
                continue
            if sentences.is_ours(path.read_text(encoding="utf-8", errors="replace")):
                path.unlink()
                done["removed"].append(str(path))
            else:
                done["kept"].append(str(path))
    return done


async def _reload_conversation(hass: HomeAssistant) -> None:
    """Home Assistant's agent reads its custom sentences again (when Assist is loaded)."""
    if not hass.services.has_service("conversation", "reload"):
        return
    try:
        await hass.services.async_call("conversation", "reload", {}, blocking=True)
    except (
        HomeAssistantError,
        OSError,
    ) as err:  # its YAML unreadable, say: the files stand
        _LOGGER.warning("Logger voice: Assist didn't reload its sentences: %s", err)


def _others_on(hass: HomeAssistant, entry_id: str) -> bool:
    return any(
        e.entry_id != entry_id and e.options.get(CONF_VOICE, DEFAULT_VOICE)
        for e in hass.config_entries.async_entries(DOMAIN)
    )


async def async_remove_sentences(
    hass: HomeAssistant, entry_id: str
) -> dict[str, list[str]]:
    """This integration's files removed — unless another Logger entry keeps them on."""
    if _others_on(hass, entry_id):
        return {"removed": [], "kept": []}
    done = await hass.async_add_executor_job(
        remove_files, Path(hass.config.path(SENTENCES_DIR))
    )
    if done["removed"]:
        await _reload_conversation(hass)
    return done


class VoiceSentences:
    """The sentences file of one Logger entry (module docstring)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: LoggerConfigEntry,
        coordinator: LoggerCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.on: bool = bool(entry.options.get(CONF_VOICE, DEFAULT_VOICE))
        self.last: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @callback
    def async_start(self) -> None:
        """Write (on) or clear (off) once Home Assistant has started; then every
        VOICE_EVERY_H while on."""
        self.entry.async_on_unload(async_at_started(self.hass, self._started))
        if self.on:
            self.entry.async_on_unload(
                async_track_time_interval(
                    self.hass, self._tick, timedelta(hours=VOICE_EVERY_H)
                )
            )

    @callback
    def _started(self, _hass: HomeAssistant) -> None:
        self.async_request_sync()

    async def _tick(self, _now: Any) -> None:
        await self.async_sync()

    @callback
    def async_request_sync(self) -> None:
        """A sync in the background (Refresh, the start): never the caller's failure."""
        self.entry.async_create_background_task(
            self.hass, self.async_sync(), f"{DOMAIN} voice sentences"
        )

    async def async_sync(self) -> None:
        """The files written from Logger's words (on), or this integration's removed (off).
        Every failure logged and kept for diagnostics, never raised."""
        async with self._lock:
            try:
                if self.on:
                    await self._write()
                else:
                    self.last = {
                        **await async_remove_sentences(self.hass, self.entry.entry_id),
                        "at": dt_util.utcnow().isoformat(),
                        "error": None,
                    }
            except LoggerError as err:
                self.last = {**self.last, "error": err.words}
                _LOGGER.warning(
                    "Logger voice: couldn't read Logger's words: %s", err.words
                )
            except OSError as err:
                self.last = {**self.last, "error": str(err)}
                _LOGGER.warning("Logger voice: couldn't write the sentences: %s", err)
            except Exception:
                self.last = {**self.last, "error": "unexpected error (see the log)"}
                _LOGGER.exception("Logger voice: the sentences failed")

    async def _write(self) -> None:
        vocab = await self.coordinator.client.command_vocab()
        integration = await async_get_integration(self.hass, DOMAIN)
        version = str(integration.version or "")
        files = {
            lang: sentences.build(vocab, lang, version) for lang in sentences.LANGUAGES
        }
        done = await self.hass.async_add_executor_job(
            write_files, Path(self.hass.config.path(SENTENCES_DIR)), files
        )
        self.last = {
            **done,
            "at": dt_util.utcnow().isoformat(),
            "counts": sentences.counts(vocab),
            "error": None,
        }
        if done["written"]:
            await _reload_conversation(self.hass)

    def diagnostics(self) -> dict[str, Any]:
        return {"on": self.on, **self.last}
