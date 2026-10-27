"""Run what Logger asks of Home Assistant: one queued action (`GET /ha/actions` → `do`), or
a Logger scene's states (the `scene.logger_*` entities and the `logger_scene` action).

Every call is blocking (the result says what happened) and the whole action has
ACTION_TIMEOUT seconds. A missing service, an entity HA doesn't have or that is unavailable,
a refusal, a timeout: ActionFailed with short words, which go back to Logger as the result.
Texts arrive filled in by Logger and go to HA as they are — never templated here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State, split_entity_id

from .catalog import notify_name
from .const import ACTION_TIMEOUT


class ActionFailed(Exception):
    """An action that didn't happen; `words` = why, for Logger."""

    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


async def async_run(hass: HomeAssistant, do: dict[str, Any]) -> str:
    """Run one action → its words (`Kitchen flashed`); ActionFailed when it didn't run."""
    runner = RUNNERS.get(str(do.get("type")))
    if runner is None:
        raise ActionFailed(f"Unknown action {do.get('type')!r}")
    return await _bounded(runner(hass, do))


async def async_apply(
    hass: HomeAssistant, name: str, entities: Any, **extra: Any
) -> Applied:
    """A Logger scene's states, entity by entity, in order. An entity HA doesn't have (or
    one unavailable) is skipped and counted; one that fails doesn't stop the rest."""
    return await _bounded(_apply(hass, name, entities, extra))


@dataclass
class Applied:
    """What a scene did: `Evening: 3 set, 1 not in Home Assistant`."""

    name: str
    done: int = 0
    missing: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def words(self) -> str:
        parts = [f"{self.done} set"]
        if self.missing:
            parts.append(f"{len(self.missing)} not in Home Assistant")
        if self.unavailable:
            parts.append(f"{len(self.unavailable)} unavailable")
        if self.failed:
            parts.append(f"{len(self.failed)} failed ({self.failed[0]})")
        return f"{self.name}: {', '.join(parts)}"


async def _bounded[T](work: Awaitable[T]) -> T:
    try:
        async with asyncio.timeout(ACTION_TIMEOUT):
            return await work
    except ActionFailed:
        raise
    except TimeoutError as err:
        raise ActionFailed(
            f"Home Assistant didn't finish within {ACTION_TIMEOUT} s"
        ) from err
    except Exception as err:
        # Whatever HA said no with (a schema, a service, the device) is the result.
        raise ActionFailed(_said(err)) from err


def _said(err: BaseException) -> str:
    return str(err).strip() or type(err).__name__


async def _call(
    hass: HomeAssistant, domain: str, service: str, data: dict[str, Any]
) -> None:
    if not hass.services.has_service(domain, service):
        raise ActionFailed(f"No {domain}.{service} in Home Assistant")
    await hass.services.async_call(domain, service, data, blocking=True)


def _entity(hass: HomeAssistant, entity_id: Any, *domains: str) -> State:
    """The target's state; ActionFailed when HA hasn't got it or it can't act now."""
    if not isinstance(entity_id, str) or "." not in entity_id:
        raise ActionFailed(f"Not an entity: {entity_id!r}")
    if domains and split_entity_id(entity_id)[0] not in domains:
        kinds = " or ".join(d.replace("_", " ") for d in domains)
        raise ActionFailed(f"{entity_id} is not a {kinds}")
    if (state := hass.states.get(entity_id)) is None:
        raise ActionFailed(f"{entity_id} is not in Home Assistant")
    if state.state == STATE_UNAVAILABLE:
        raise ActionFailed(f"{entity_id} is unavailable")
    return state


def _on_off(value: Any) -> bool:
    if value not in ("on", "off"):
        raise ActionFailed(f"Unknown state {value!r}")
    return value == "on"


def _light_on(row: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """`brightness` (1 to 100 %) and `color_temp_k` → HA's names; only those set."""
    data = dict(extra)
    if row.get("brightness") is not None:
        data["brightness_pct"] = row["brightness"]
    if row.get("color_temp_k") is not None:
        data["color_temp_kelvin"] = row["color_temp_k"]
    return data


async def _scene(hass: HomeAssistant, do: dict[str, Any]) -> str:
    state = _entity(hass, do.get("entity_id"), "scene")
    await _call(hass, "scene", "turn_on", {ATTR_ENTITY_ID: state.entity_id})
    return f"{state.name} on"


async def _script(hass: HomeAssistant, do: dict[str, Any]) -> str:
    state = _entity(hass, do.get("entity_id"), "script")
    await _call(hass, "script", "turn_on", {ATTR_ENTITY_ID: state.entity_id})
    return f"{state.name} started"


async def _light(hass: HomeAssistant, do: dict[str, Any]) -> str:
    state = _entity(hass, do.get("entity_id"), "light")
    target = {ATTR_ENTITY_ID: state.entity_id}
    if do.get("state") == "flash":
        await _call(hass, "light", "turn_on", {**target, "flash": "long"})
        return f"{state.name} flashed"
    if _on_off(do.get("state")):
        await _call(hass, "light", "turn_on", _light_on(do, target))
        return f"{state.name} on"
    await _call(hass, "light", "turn_off", target)
    return f"{state.name} off"


async def _switch(hass: HomeAssistant, do: dict[str, Any]) -> str:
    state = _entity(hass, do.get("entity_id"), "switch")
    on = _on_off(do.get("state"))
    await _call(
        hass,
        "switch",
        "turn_on" if on else "turn_off",
        {ATTR_ENTITY_ID: state.entity_id},
    )
    return f"{state.name} {'on' if on else 'off'}"


async def _announce(hass: HomeAssistant, do: dict[str, Any]) -> str:
    """A satellite announces itself; a media player speaks through a TTS engine (Logger's
    pick, else the first HA has)."""
    target = _entity(hass, do.get("target"), "media_player", "assist_satellite")
    text = str(do.get("text") or "")
    if target.domain == "assist_satellite":
        await _call(
            hass,
            "assist_satellite",
            "announce",
            {ATTR_ENTITY_ID: target.entity_id, "message": text},
        )
    else:
        engine = do.get("engine") or next(
            iter(sorted(hass.states.async_entity_ids("tts"))), None
        )
        if engine is None:
            raise ActionFailed("No text-to-speech engine in Home Assistant")
        _entity(hass, engine, "tts")
        await _call(
            hass,
            "tts",
            "speak",
            {
                ATTR_ENTITY_ID: engine,
                "media_player_entity_id": target.entity_id,
                "message": text,
            },
        )
    return f"Announced on {target.name}"


async def _notify(hass: HomeAssistant, do: dict[str, Any]) -> str:
    service = do.get("service")
    if not isinstance(service, str) or not service.startswith("notify."):
        raise ActionFailed(f"Not a notify service: {service!r}")
    data = {"message": str(do.get("text") or "")}
    if do.get("title"):
        data["title"] = str(do["title"])
    await _call(hass, "notify", service.removeprefix("notify."), data)
    return f"Sent to {notify_name(hass, service)}"


async def _logger_scene(hass: HomeAssistant, do: dict[str, Any]) -> str:
    applied = await _apply(hass, str(do.get("name") or "Scene"), do.get("entities"), {})
    if applied.failed:
        raise ActionFailed(applied.words)
    return applied.words


async def _apply(
    hass: HomeAssistant, name: str, entities: Any, extra: dict[str, Any]
) -> Applied:
    applied = Applied(name)
    rows = entities if isinstance(entities, list) else []
    for row in rows:
        entity_id = row.get(ATTR_ENTITY_ID) if isinstance(row, dict) else None
        state = hass.states.get(entity_id) if isinstance(entity_id, str) else None
        if state is None:
            applied.missing.append(str(entity_id))
            continue
        if state.state == STATE_UNAVAILABLE:
            applied.unavailable.append(state.entity_id)
            continue
        try:
            await _set(hass, state, row, extra)
        except Exception as err:
            # Counted; the rest of the scene still applies.
            words = err.words if isinstance(err, ActionFailed) else _said(err)
            applied.failed.append(f"{state.entity_id}: {words}")
        else:
            applied.done += 1
    return applied


async def _set(
    hass: HomeAssistant, state: State, row: dict[str, Any], extra: dict[str, Any]
) -> None:
    """One entity of a scene: light, switch, media player; anything else on/off."""
    on = _on_off(row.get("state"))
    target = {ATTR_ENTITY_ID: state.entity_id}
    if state.domain == "light":
        light = dict(target)
        if "transition" in extra:
            light["transition"] = extra["transition"]
        if on:
            await _call(hass, "light", "turn_on", _light_on(row, light))
        else:
            await _call(hass, "light", "turn_off", light)
    elif state.domain == "media_player":
        await _media_player(hass, state, row, on)
    elif state.domain == "switch":
        await _call(hass, "switch", "turn_on" if on else "turn_off", target)
    else:
        await _call(hass, "homeassistant", "turn_on" if on else "turn_off", target)


async def _media_player(
    hass: HomeAssistant, state: State, row: dict[str, Any], on: bool
) -> None:
    """On → turned on (when it can be), then its volume and source; off → turned off (a
    speaker that can't be: stopped)."""
    target = {ATTR_ENTITY_ID: state.entity_id}
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
    can = MediaPlayerEntityFeature(features if isinstance(features, int) else 0)
    if not on:
        if MediaPlayerEntityFeature.TURN_OFF not in can and (
            MediaPlayerEntityFeature.STOP in can
        ):
            await _call(hass, "media_player", "media_stop", target)
        else:
            await _call(hass, "media_player", "turn_off", target)
        return
    if MediaPlayerEntityFeature.TURN_ON in can:
        await _call(hass, "media_player", "turn_on", target)
    if row.get("volume") is not None:
        await _call(
            hass,
            "media_player",
            "volume_set",
            {**target, "volume_level": row["volume"] / 100},
        )
    if row.get("source"):
        await _call(
            hass, "media_player", "select_source", {**target, "source": row["source"]}
        )


RUNNERS: dict[str, Callable[[HomeAssistant, dict[str, Any]], Awaitable[str]]] = {
    "scene": _scene,
    "script": _script,
    "light": _light,
    "switch": _switch,
    "announce": _announce,
    "notify": _notify,
    "logger_scene": _logger_scene,
}
