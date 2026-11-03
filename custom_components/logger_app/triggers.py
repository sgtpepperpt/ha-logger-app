"""Home Assistant triggers for server rules."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any

from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.trigger import (
    async_initialize_triggers,
    async_validate_trigger_config,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REFUSED = "Home Assistant refused the trigger: {}"
NOT_A_DEVICE = "Not a device trigger — Logger sends only device triggers for buttons"
TEMPLATED = ("variables", "enabled")


class TriggerRefused(Exception):
    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


def trigger_config(when: Any) -> dict[str, Any]:
    kind = when.get("type") if isinstance(when, dict) else None
    if kind == "device":
        if not isinstance(trigger := when.get("trigger"), dict):
            raise TriggerRefused("Home Assistant no longer lists this device trigger")
        platform = [trigger[k] for k in ("trigger", "platform") if k in trigger]
        if platform != ["device"] or not all(
            isinstance(trigger.get(k), str) and trigger[k]
            for k in ("domain", "device_id")
        ):
            raise TriggerRefused(NOT_A_DEVICE)
        return {k: v for k, v in trigger.items() if k not in TEMPLATED}
    if kind == "zone":
        return {
            "trigger": "zone",
            CONF_ENTITY_ID: when.get("person"),
            "zone": when.get("zone"),
            "event": when.get("event"),
        }
    if kind == "state":
        config = {
            "trigger": "state",
            CONF_ENTITY_ID: when.get(CONF_ENTITY_ID),
            "to": when.get("to"),
        }
        if when.get("from") is not None:
            config["from"] = when["from"]
        if (seconds := when.get("for_s")) is not None:
            if not isinstance(seconds, int | float) or isinstance(seconds, bool):
                raise TriggerRefused(f"Not a number of seconds: {seconds!r}")
            config["for"] = {"seconds": seconds}
        return config
    raise TriggerRefused(f"Unknown trigger type {kind!r}")


def config_key(config: dict[str, Any]) -> str:
    return json.dumps(config, sort_keys=True, default=str)


async def async_attach(
    hass: HomeAssistant,
    rule: dict[str, Any],
    config: dict[str, Any],
    action: Callable[..., Any],
) -> CALLBACK_TYPE:
    name = f"Logger rule {rule.get('name') or rule.get('id')}"
    try:
        configs = await async_validate_trigger_config(hass, cv.TRIGGER_SCHEMA([config]))
    except Exception as err:
        raise TriggerRefused(REFUSED.format(_said(err))) from err

    errors: list[str] = []

    def log_cb(level: int, msg: str, **kwargs: Any) -> None:
        if level >= logging.ERROR:
            cause = kwargs.get("exc_info")
            errors.append(
                _said(cause)
                if isinstance(cause, BaseException)
                else msg.removeprefix("Got error '").removesuffix(
                    "' when setting up triggers for"
                )
            )
        _LOGGER.debug("%s: %s", name, msg)

    remove = await async_initialize_triggers(
        hass, configs, action, DOMAIN, name, log_cb
    )
    if remove is None:
        raise TriggerRefused(REFUSED.format("; ".join(errors) or "it did not attach"))
    return remove


def _said(err: BaseException) -> str:
    return str(err).strip() or type(err).__name__
