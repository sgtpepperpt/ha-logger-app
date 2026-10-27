"""The base of every Logger entity: one device per Logger, keyed by its `instance_id`."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import LoggerCoordinator


class LoggerEntity(CoordinatorEntity[LoggerCoordinator]):
    """`unique_id` = `<instance_id>_<key>`: a new address or a restored backup is the same
    Logger, with the same entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LoggerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        instance_id = coordinator.about.instance_id
        self._attr_unique_id = f"{instance_id}_{key}"
        # Keyword arguments only: DeviceInfo is a TypedDict up to HA 2026.10, a dataclass after.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, instance_id)},
            name=NAME,
            configuration_url=coordinator.client.url,
            sw_version=coordinator.about.code,
            entry_type=DeviceEntryType.SERVICE,
        )
