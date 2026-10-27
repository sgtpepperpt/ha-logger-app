"""*Download diagnostics*: the entry, Logger's `/about`, the last `/status`, `/quick`, `/alerts`.

Nothing redacted: the address is a LAN one (no credentials exist — Logger's LAN port has no
sign-in) and the data is the user's own, downloaded by them.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import LoggerConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LoggerConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "about": asdict(coordinator.about),
        "last_update_success": coordinator.last_update_success,
        "status": data.status if data else None,
        "quick": data.quick if data else None,
        "alerts": data.alerts if data else None,
    }
