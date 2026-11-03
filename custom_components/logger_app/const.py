"""Constants."""

from typing import Final

DOMAIN: Final = "logger_app"
NAME: Final = "Logger"

API_MAX: Final = 1

CONF_TZ: Final = "tz"
CONF_ALERTS: Final = "alerts"
CONF_VOICE: Final = "voice"

LAN_EXAMPLE: Final = "http://<host>:4173"
DEFAULT_URL: Final = "http://logger.local:4173"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 600
DEFAULT_ALERTS: Final = True
DEFAULT_VOICE: Final = False

QUICK_EVERY: Final = 10

STALE_MIN: Final = 10
HIDE_MIN: Final = 24 * 60

KCAL_MAX: Final = 20000

EVENT_FIRED: Final = "logger_app_fired"

SERVICE_FIRE: Final = "fire"
SERVICE_SHOPPING_ADD: Final = "shopping_add"
SERVICE_TABLET_WAKE: Final = "tablet_wake"

FEATURE_RULES: Final = "rules"
FEATURE_CALENDAR: Final = "calendar"
FEATURE_VOICE: Final = "voice"
SENTENCES_DIR: Final = "custom_sentences"
SENTENCES_FILE: Final = "logger.yaml"
SHARE_DIR: Final = "/share"
SPEECH_TO_PHRASE_DIR: Final = "/share/speech-to-phrase/custom_sentences"
VOICE_EVERY_H: Final = 24
CALENDAR_CHUNK_DAYS: Final = 365
CALENDAR_MAX_CHUNKS: Final = 5
ACTION_TIMEOUT: Final = 30
CATALOG_DEBOUNCE: Final = 10
CATALOG_EVERY_H: Final = 24
CATALOG_RETRY: Final = 300
FIRE_RETRY_DELAYS: Final = (30, 90, 180)
RETRY_REFUSED_EVERY: Final = 10
