"""Scenes: one per Logger scene (made in Logger → Settings → Home Assistant → Scenes).

Logger owns them; Home Assistant shows them as `scene.logger_<name>` (activatable from the
UI, a dashboard, Assist). Activating one applies its stored states entity by entity
(actions.py — the `logger_scene` action does the same): an entity Home Assistant doesn't
have is skipped and counted, one that fails is an error once the rest is applied. They
follow Logger's `scenes_rev`: a scene added in Logger appears, one deleted is removed from
the entity registry (not left unavailable). Only with the rules feature on; a Logger
without it has no scenes here.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.scene import DOMAIN as SCENE_DOMAIN, Scene
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import actions
from .const import DOMAIN
from .coordinator import LoggerConfigEntry, LoggerCoordinator
from .entity import LoggerEntity
from .rules import LoggerRules

PARALLEL_UPDATES = 0  # activations don't touch Logger


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LoggerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    rules = coordinator.rules
    registry = er.async_get(hass)
    prefix = f"{coordinator.about.instance_id}_scene_"
    known: set[int] = set()

    @callback
    def sync() -> None:
        """After a read of Logger's scenes (with the feature off: there are none)."""
        scenes = rules.scenes if rules is not None else {}
        if scenes is None:
            return  # not read yet: the registry stays as it is
        if rules is not None and (new := set(scenes) - known):
            known.update(new)
            async_add_entities(LoggerScene(coordinator, rules, i) for i in sorted(new))
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            if reg.domain != SCENE_DOMAIN or not reg.unique_id.startswith(prefix):
                continue
            scene_id = reg.unique_id.removeprefix(prefix)
            if not scene_id.isdigit() or int(scene_id) not in scenes:
                registry.async_remove(reg.entity_id)
        known.intersection_update(scenes)

    sync()
    if rules is not None:
        entry.async_on_unload(rules.async_add_scenes_listener(sync))


class LoggerScene(LoggerEntity, Scene):
    """A Logger scene: named and iconed as in Logger."""

    def __init__(
        self, coordinator: LoggerCoordinator, rules: LoggerRules, scene_id: int
    ) -> None:
        super().__init__(coordinator, f"scene_{scene_id}")
        self.rules = rules
        self.scene_id = scene_id
        self._show()

    def _scene(self) -> dict[str, Any] | None:
        return (self.rules.scenes or {}).get(self.scene_id)

    def _show(self) -> None:
        if (scene := self._scene()) is None:
            return
        self._attr_name = str(scene.get("name") or f"Scene {self.scene_id}")
        icon = scene.get("icon")
        self._attr_icon = icon if isinstance(icon, str) and ":" in icon else None
        entities = scene.get("entities")
        self._attr_extra_state_attributes = {
            "id": self.scene_id,
            "entity_id": [
                e.get("entity_id")
                for e in entities
                if isinstance(e, dict) and isinstance(e.get("entity_id"), str)
            ]
            if isinstance(entities, list)
            else [],
        }

    @property
    def available(self) -> bool:
        """Its states are here: it works while Logger is restarting, too."""
        return self._scene() is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.rules.async_add_scenes_listener(self._renamed))

    @callback
    def _renamed(self) -> None:
        if self._scene() is not None:  # a deleted one is being removed
            self._show()
            self.async_write_ha_state()

    async def async_activate(self, **kwargs: Any) -> None:
        scene = self._scene() or {}
        try:
            applied = await actions.async_apply(
                self.hass,
                str(scene.get("name") or f"Scene {self.scene_id}"),
                scene.get("entities"),
                **kwargs,
            )
        except actions.ActionFailed as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="scene_failed",
                translation_placeholders={"words": err.words},
            ) from err
        if applied.failed:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="scene_failed",
                translation_placeholders={"words": applied.words},
            )
