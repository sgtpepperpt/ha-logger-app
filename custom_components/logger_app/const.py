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

# Rules + scenes (F39), only with a Logger whose `/about.features` has FEATURE_RULES: the
# rules and scenes live in Logger (Settings → Home Assistant); this integration is its hands.
FEATURE_RULES: Final = "rules"
# An action Logger queued, or a Logger scene, may take this long in Home Assistant (s).
ACTION_TIMEOUT: Final = 30
# What HA has (`PUT /ha/catalog`): again after a registry change has been quiet this long (s),
# and at least this often (h) — plus whenever Logger asks for it.
CATALOG_DEBOUNCE: Final = 10
CATALOG_EVERY_H: Final = 24
# After Logger refused a catalog (a 4xx), wait this long before the next try (s), doubling
# with each refusal up to 6 h — unless a nudge asks for one.
CATALOG_RETRY: Final = 300
# A rule's HA trigger fired while Logger didn't answer: the same body again after these
# waits (s), then given up (logged).
FIRE_RETRY_DELAYS: Final = (30, 90, 180)
# A rule HA refused (its device's integration still loading, say): tried again every this
# many cycles.
RETRY_REFUSED_EVERY: Final = 10
