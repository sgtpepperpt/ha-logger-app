"""Constants of the Logger integration (domain `logger_app`: HA core owns `logger`)."""

from typing import Final

DOMAIN: Final = "logger_app"
NAME: Final = "Logger"

# The contract: Logger's `GET /api/about` → `api`, bumped by Logger only when a shape this
# integration reads breaks (tests/fixtures/ha_contract.json). A Logger answering a higher `api`
# is refused (`unsupported_api`): update the integration.
API_MAX: Final = 1

CONF_TZ: Final = "tz"
CONF_ALERTS: Final = "alerts"

# The LAN port (no sign-in); the public address answers with its sign-in page.
LAN_EXAMPLE: Final = "http://<host>:4173"
DEFAULT_URL: Final = "http://logger.local:4173"

DEFAULT_SCAN_INTERVAL: Final = 60  # s
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 600
DEFAULT_ALERTS: Final = True

# Logger counts no change kind for quick entries (its data_change kinds: food, body, blood,
# journal, meds, goals, pressure, workouts, tasks), so `/quick` is read on the first poll and
# on every QUICK_EVERY-th one after it — and at once after a press Logger answers 404 to.
QUICK_EVERY: Final = 10

# Logger's glucose rules (frontend lib/glucose/glucoseNow.ts = the Android widgets'): a
# reading older than STALE_MIN is stale; older than HIDE_MIN it is not shown at all.
STALE_MIN: Final = 10
HIDE_MIN: Final = 24 * 60

KCAL_MAX: Final = 20000  # Logger's quick.KCAL_MAX

EVENT_FIRED: Final = "logger_app_fired"

SERVICE_FIRE: Final = "fire"
SERVICE_SHOPPING_ADD: Final = "shopping_add"
SERVICE_TABLET_WAKE: Final = "tablet_wake"
