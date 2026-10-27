"""Yes/no questions on Logger's `/status`; None = unknown (Logger said null)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import STALE_MIN
from .coordinator import (
    LoggerConfigEntry,
    LoggerCoordinator,
    LoggerData,
    count,
    dig,
    flag,
    glucose_age,
    number,
)
from .entity import LoggerEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class LoggerBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[LoggerData], bool | None]
    attrs_fn: Callable[[LoggerData], dict[str, Any]] | None = None


def _more_than_none(value: int | float | None) -> bool | None:
    return None if value is None else value > 0


def _glucose_low(data: LoggerData) -> bool | None:
    """Below the low line — asked of a fresh reading only (a stale one says nothing now)."""
    age = glucose_age(data)
    mgdl = number(dig(data.status, "glucose", "mgdl"))
    low = number(dig(data.status, "glucose", "low"))
    if age is None or age > STALE_MIN or mgdl is None or low is None:
        return None
    return mgdl < low


def _glucose_stale(data: LoggerData) -> bool | None:
    age = glucose_age(data)
    return None if age is None else age > STALE_MIN


def _cgm_attrs(data: LoggerData) -> dict[str, Any]:
    cgm = dig(data.status, "glucose", "sensor")
    if not isinstance(cgm, dict):
        return {}
    return {"ends": cgm.get("ends"), "expired": cgm.get("expired")}


BINARY_SENSORS: tuple[LoggerBinarySensorDescription, ...] = (
    LoggerBinarySensorDescription(
        key="medication_overdue",
        translation_key="medication_overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: _more_than_none(
            count(dig(d.status, "medication", "overdue"))
        ),
    ),
    LoggerBinarySensorDescription(
        key="tasks_overdue",
        translation_key="tasks_overdue",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: _more_than_none(number(dig(d.status, "tasks", "overdue"))),
    ),
    LoggerBinarySensorDescription(
        key="glucose_low",
        translation_key="glucose_low",
        device_class=BinarySensorDeviceClass.SAFETY,
        is_on_fn=_glucose_low,
    ),
    LoggerBinarySensorDescription(
        key="glucose_stale",
        translation_key="glucose_stale",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_glucose_stale,
    ),
    LoggerBinarySensorDescription(
        key="tasks_read_only",
        translation_key="tasks_read_only",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: flag(dig(d.status, "tasks", "read_only")),
    ),
    LoggerBinarySensorDescription(
        key="sensor_ending_soon",
        translation_key="sensor_ending_soon",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda d: flag(dig(d.status, "glucose", "sensor", "ending_soon")),
        attrs_fn=_cgm_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(LoggerBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class LoggerBinarySensor(LoggerEntity, BinarySensorEntity):
    entity_description: LoggerBinarySensorDescription

    def __init__(
        self,
        coordinator: LoggerCoordinator,
        description: LoggerBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
