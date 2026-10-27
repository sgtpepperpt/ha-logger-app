"""Logger's `/status` as sensors (+ the `/alerts` count with the alerts option on)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfBloodGlucoseConcentration
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN, HIDE_MIN
from .coordinator import (
    LoggerConfigEntry,
    LoggerCoordinator,
    LoggerData,
    count,
    dig,
    glucose_age,
    instant,
    number,
)
from .entity import LoggerEntity

PARALLEL_UPDATES = 0  # coordinator-driven: nothing to throttle

KCAL = "kcal"


@dataclass(frozen=True, kw_only=True)
class LoggerSensorDescription(SensorEntityDescription):
    """`value_fn` reads one poll; None = unknown (Logger's null, never 0)."""

    value_fn: Callable[[LoggerData], StateType | datetime]
    attrs_fn: Callable[[LoggerData], dict[str, Any]] | None = None


def _food(key: str) -> LoggerSensorDescription:
    return LoggerSensorDescription(
        key=f"{key}_kcal",
        translation_key=f"{key}_kcal",
        native_unit_of_measurement=KCAL,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: number(dig(d.status, "food", key)),
    )


type Row = dict[str, Any]


def _keys(*keys: str) -> Callable[[Row], Row]:
    return lambda row: {k: row.get(k) for k in keys}


def _slot_row(row: Row) -> Row:
    """A medication / Daily Care slot as a card shows it."""
    return {
        "name": row.get("name"),
        "words": row.get("words"),
        "due": dig(row, "due", "from"),
        "period": row.get("period"),
    }


def _rows(
    rows: Any, pick: Callable[[Row], Row], keep: Callable[[Row], bool] = lambda _: True
) -> dict[str, Any]:
    """`{"rows": […]}` of Logger's rows; {} when Logger sent none (its half is null — the
    state is unknown then)."""
    if not isinstance(rows, list):
        return {}
    return {"rows": [pick(r) for r in rows if isinstance(r, dict) and keep(r)]}


def _slots(half: str, which: str) -> LoggerSensorDescription:
    """`medication_overdue` …: how many slots, the rows as attributes. `medication` / `care`
    is null on a day with nothing scheduled → unknown."""
    return LoggerSensorDescription(
        key=f"{half}_{which}",
        translation_key=f"{half}_{which}",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: count(dig(d.status, half, which)),
        attrs_fn=lambda d: _rows(dig(d.status, half, which), _slot_row),
    )


def _task_rows(data: LoggerData, kind: str, overdue: bool) -> dict[str, Any]:
    """The rows Logger shipped for this count (at most 8 in all, overdue first)."""
    return _rows(
        dig(data.status, "tasks", "rows"),
        _keys("summary", "due", "words", "list"),
        lambda r: r.get("kind") == kind and bool(r.get("overdue")) is overdue,
    )


def _tasks(
    key: str, path: tuple[str, ...], kind: str, overdue: bool
) -> LoggerSensorDescription:
    return LoggerSensorDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: number(dig(d.status, "tasks", *path)),
        attrs_fn=lambda d: _task_rows(d, kind, overdue),
    )


def _glucose(data: LoggerData) -> StateType:
    """mg/dL; unknown once the reading is older than a day (Logger's widgets hide it)."""
    age = glucose_age(data)
    if age is None or age > HIDE_MIN:
        return None
    return number(dig(data.status, "glucose", "mgdl"))


def _glucose_attrs(data: LoggerData) -> dict[str, Any]:
    glucose = dig(data.status, "glucose")
    if not isinstance(glucose, dict):
        return {}
    return {
        "direction": glucose.get("direction"),
        "at": glucose.get("at"),
        "source": glucose.get("source"),
        "low": glucose.get("low"),
        "high": glucose.get("high"),
        "minutes_ago": glucose_age(data),
    }


def _cgm_attrs(data: LoggerData) -> dict[str, Any]:
    cgm = dig(data.status, "glucose", "sensor")
    if not isinstance(cgm, dict):
        return {}
    keys = ("of", "started", "ends", "serial", "via", "expired", "words")
    return {k: cgm.get(k) for k in keys}


def _next_attrs(data: LoggerData) -> dict[str, Any]:
    nxt = dig(data.status, "medication", "next")
    if not isinstance(nxt, dict):
        return {}
    return {"name": nxt.get("name"), "words": nxt.get("words")}


def _refill_attrs(data: LoggerData) -> dict[str, Any]:
    return _rows(
        dig(data.status, "refill", "soon"),
        _keys("name", "days_left", "refill_by", "approx"),
    )


def _steps_attrs(data: LoggerData) -> dict[str, Any]:
    steps = dig(data.status, "steps")
    if not isinstance(steps, dict):
        return {}
    return {"day": steps.get("day"), "as_of": steps.get("as_of")}


SENSORS: tuple[LoggerSensorDescription, ...] = (
    _food("free"),
    _food("eaten"),
    _food("planned"),
    _food("goal"),
    _food("banked"),
    LoggerSensorDescription(
        key="glucose",
        translation_key="glucose",
        device_class=SensorDeviceClass.BLOOD_GLUCOSE_CONCENTRATION,
        native_unit_of_measurement=UnitOfBloodGlucoseConcentration.MILLIGRAMS_PER_DECILITER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_glucose,
        attrs_fn=_glucose_attrs,
    ),
    _slots("medication", "overdue"),
    _slots("medication", "due"),
    _slots("care", "overdue"),
    _slots("care", "due"),
    LoggerSensorDescription(
        key="medication_next",
        translation_key="medication_next",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: instant(dig(d.status, "medication", "next", "due", "from")),
        attrs_fn=_next_attrs,
    ),
    LoggerSensorDescription(
        key="steps_today",
        translation_key="steps_today",
        native_unit_of_measurement="steps",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: number(dig(d.status, "steps", "today")),
        attrs_fn=_steps_attrs,
    ),
    _tasks("tasks_overdue", ("overdue",), "task", True),
    _tasks("tasks_today", ("today",), "task", False),
    _tasks("deliveries_overdue", ("deliveries", "overdue"), "delivery", True),
    _tasks("deliveries_today", ("deliveries", "today"), "delivery", False),
    LoggerSensorDescription(
        key="tasks_waiting",
        translation_key="tasks_waiting",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: number(dig(d.status, "tasks", "waiting")),
    ),
    LoggerSensorDescription(
        key="refill_soon",
        translation_key="refill_soon",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: number(dig(d.status, "refill", "n")),
        attrs_fn=_refill_attrs,
    ),
    LoggerSensorDescription(
        key="sensor_day",
        translation_key="sensor_day",
        value_fn=lambda d: number(dig(d.status, "glucose", "sensor", "day")),
        attrs_fn=_cgm_attrs,
    ),
    LoggerSensorDescription(
        key="sensor_ends",
        translation_key="sensor_ends",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: instant(dig(d.status, "glucose", "sensor", "ends")),
    ),
)

PROBLEMS = LoggerSensorDescription(
    key="problems",
    translation_key="problems",
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda d: count(d.alerts),
    attrs_fn=lambda d: {
        "texts": [a.get("text") for a in d.alerts or [] if a.get("text")]
    },
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    descriptions = list(SENSORS)
    if coordinator.alerts_on:
        descriptions.append(PROBLEMS)
    else:
        # The option turned off: the sensor goes, not left behind as unavailable.
        registry = er.async_get(hass)
        unique_id = f"{coordinator.about.instance_id}_{PROBLEMS.key}"
        if entity_id := registry.async_get_entity_id(SENSOR_DOMAIN, DOMAIN, unique_id):
            registry.async_remove(entity_id)
    async_add_entities(LoggerSensor(coordinator, d) for d in descriptions)


class LoggerSensor(LoggerEntity, SensorEntity):
    entity_description: LoggerSensorDescription
    # Changes every poll; history keeps the reading's own `at`.
    _unrecorded_attributes = frozenset({"minutes_ago"})

    def __init__(
        self, coordinator: LoggerCoordinator, description: LoggerSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
