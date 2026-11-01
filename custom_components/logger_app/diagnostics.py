"""*Download diagnostics*: the entry, Logger's `/about`, the last `/status`, `/quick`, `/alerts`,
and — with the rules feature on — the state of the rules, scenes, catalog and webhook; with the
voice feature, the sentences file's last build (where written, the lists' sizes, an error).

Nothing else redacted: the address is a LAN one (no credentials exist — Logger's LAN port has
no sign-in) and the data is the user's own, downloaded by them. The webhook id is the one
secret (anyone on the LAN who has it can nudge): only its first 4 characters are shown.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant

from .const import FEATURE_RULES, FEATURE_VOICE
from .coordinator import LoggerConfigEntry
from .rules import mask


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LoggerConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    entry_data = dict(entry.data)
    if isinstance(entry_data.get(CONF_WEBHOOK_ID), str):
        entry_data[CONF_WEBHOOK_ID] = mask(entry_data[CONF_WEBHOOK_ID])
    rules = coordinator.rules
    return {
        "entry": {
            "data": entry_data,
            "options": dict(entry.options),
            "unique_id": entry.unique_id,
        },
        "about": {
            **asdict(coordinator.about),
            "features": list(coordinator.about.features),
        },
        "last_update_success": coordinator.last_update_success,
        "status": data.status if data else None,
        "quick": data.quick if data else None,
        "alerts": data.alerts if data else None,
        "rules": {
            "feature": FEATURE_RULES in coordinator.about.features,
            **(rules.diagnostics() if rules is not None else {}),
        },
        "voice": {
            "feature": FEATURE_VOICE in coordinator.about.features,
            **(
                coordinator.voice.diagnostics() if coordinator.voice is not None else {}
            ),
        },
    }
