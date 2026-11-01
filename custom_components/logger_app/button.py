"""Buttons: one per quick entry (made in Logger → Settings → Quick entries), the tablet wake,
and a refresh.

The quick-entry buttons follow `/quick`: an entry added in Logger gets its button on the next
read of the list, one deleted loses it (removed from the entity registry, not left as
unavailable). A press fires the entry with `tz`; Logger's refusal (a tick already taken) is an
HA error carrying Logger's sentence.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import LoggerConfigEntry, LoggerCoordinator
from .entity import LoggerEntity

PARALLEL_UPDATES = 1  # one write at a time per platform

# Logger's quick-entry glyphs (frontend lib/ui/icons.ts `quickIcons`, lucide) → MDI.
QUICK_ICONS = {
    "notebook": "mdi:notebook-edit",
    "activity": "mdi:pulse",
    "pill": "mdi:pill",
    "utensils": "mdi:silverware-fork-knife",
    "house": "mdi:home",
    "coffee": "mdi:coffee",
    "cooking": "mdi:pot-steam",
    "apple": "mdi:food-apple",
    "water": "mdi:cup-water",
    "wine": "mdi:glass-wine",
    "dumbbell": "mdi:dumbbell",
    "bike": "mdi:bike",
    "walk": "mdi:walk",
    "bed": "mdi:bed",
    "moon": "mdi:weather-night",
    "sun": "mdi:white-balance-sunny",
    "heart": "mdi:heart",
    "thermometer": "mdi:thermometer",
    "drop": "mdi:water",
    "dog": "mdi:dog",
    "cart": "mdi:cart",
    "zap": "mdi:flash",
}
# A kind's glyph when the entry picked none (`quickKindIcons`).
KIND_ICONS = {
    "diary": "mdi:notebook-edit",
    "symptom": "mdi:pulse",
    "intake": "mdi:pill",
    "placeholder": "mdi:silverware-fork-knife",
    "task_nudge": "mdi:home",
    "shopping_add": "mdi:cart",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [LoggerTabletWakeButton(coordinator), LoggerRefreshButton(coordinator)]
    )
    registry = er.async_get(hass)
    prefix = f"{coordinator.about.instance_id}_quick_"
    known: set[int] = set()

    @callback
    def sync_quick() -> None:
        ids = {e["id"] for e in coordinator.data.quick}
        if new := ids - known:
            known.update(new)
            async_add_entities(LoggerQuickButton(coordinator, i) for i in sorted(new))
        # Gone from Logger — also those deleted while HA was off.
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.domain != BUTTON_DOMAIN or not reg.unique_id.startswith(prefix):
                continue
            quick_id = reg.unique_id.removeprefix(prefix)
            if not quick_id.isdigit() or int(quick_id) not in ids:
                registry.async_remove(reg.entity_id)
        known.intersection_update(ids)

    sync_quick()
    entry.async_on_unload(coordinator.async_add_listener(sync_quick))


class LoggerQuickButton(LoggerEntity, ButtonEntity):
    """A quick entry: named and drawn as in Logger."""

    def __init__(self, coordinator: LoggerCoordinator, quick_id: int) -> None:
        super().__init__(coordinator, f"quick_{quick_id}")
        self.quick_id = quick_id
        self._show(self._entry())

    def _entry(self) -> dict[str, Any] | None:
        return next(
            (e for e in self.coordinator.data.quick if e["id"] == self.quick_id), None
        )

    def _show(self, entry: dict[str, Any] | None) -> None:
        if entry is None:
            return
        self._attr_name = str(entry.get("name") or f"Quick entry {self.quick_id}")
        self._attr_icon = QUICK_ICONS.get(entry.get("icon")) or KIND_ICONS.get(
            entry.get("kind"), "mdi:gesture-tap-button"
        )
        self._attr_extra_state_attributes = {
            "id": self.quick_id,
            "kind": entry.get("kind"),
            "about": entry.get("about"),
        }

    @property
    def available(self) -> bool:
        return super().available and self._entry() is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._show(self._entry())
        super()._handle_coordinator_update()

    async def async_press(self) -> None:
        await self.coordinator.async_fire(self.quick_id)


class LoggerTabletWakeButton(LoggerEntity, ButtonEntity):
    """Wake the kitchen tablet (Logger → Settings → Tablet)."""

    _attr_translation_key = "tablet_wake"

    def __init__(self, coordinator: LoggerCoordinator) -> None:
        super().__init__(coordinator, "tablet_wake")

    async def async_press(self) -> None:
        await self.coordinator.async_tablet_wake()


class LoggerRefreshButton(LoggerEntity, ButtonEntity):
    """Read Logger again now — `/status`, the quick entries, and its words for the voice
    sentences when they are on."""

    _attr_translation_key = "refresh"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LoggerCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    @property
    def available(self) -> bool:
        return True  # most wanted while the last poll failed

    async def async_press(self) -> None:
        self.coordinator.read_quick_next()
        if (voice := self.coordinator.voice) is not None and voice.on:
            voice.async_request_sync()
        await self.coordinator.async_refresh()
