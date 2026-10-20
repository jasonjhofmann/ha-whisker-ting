"""Constants for the Whisker Ting integration."""

DOMAIN = "whisker_ting"

# AWS Cognito Configuration
COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_trW4gH661"
COGNITO_CLIENT_ID = "4akjeqt9gtl8rgg1cksunipk9u"

# API Configuration
API_BASE_URL = "https://api.wskr.io"
API_USERS_ENDPOINT = "/api/v1/Users/{user_id}"
# Per-request ceiling for the REST API and Cognito. Home Assistant's shared
# session otherwise allows 5 minutes, long enough to stall the hazard poll.
REQUEST_TIMEOUT = 30  # seconds

# SignalR WebSocket
SIGNALR_URL = "wss://signalr.api.wskr.io/dataHub"

# Update interval
DEFAULT_SCAN_INTERVAL = 60  # seconds
MIN_SCAN_INTERVAL = 30  # seconds
MAX_SCAN_INTERVAL = 3600  # seconds (1 hour)

# Real-time voltage publish throttle (state-write rate limit).
# The stream arrives at ~4 Hz; publishing every sample would write
# ~345,000 recorder rows per voltage entity per day.
DEFAULT_VOLTAGE_PUBLISH_INTERVAL = 5  # seconds
MIN_VOLTAGE_PUBLISH_INTERVAL = 1  # seconds
MAX_VOLTAGE_PUBLISH_INTERVAL = 60  # seconds

# Config keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USER_ID = "user_id"
CONF_API_KEY = "api_key"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ID_TOKEN = "id_token"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_VOLTAGE_PUBLISH_INTERVAL = "voltage_publish_interval"
# Options key written by the retired 3.0.x station-id probe. Only read by the
# config-entry migration, which removes it.
CONF_STATION_IDS = "station_ids"

# Device types
DEVICE_TYPE_FIRE_SENSOR = "FireSensor"

# Hazard status values
HAZARD_STATUS_NO_HAZARD = "No Hazards Detected"
HAZARD_STATUS_REVIEWED_NOT_FIRE = "ReviewedNotFire"

# Notifications
API_NOTIFICATIONS_ENDPOINT = "/api/v1/Notifications/history/{user_id}"

CONF_ALERT_NOTIFICATIONS = "alert_notifications"
DEFAULT_ALERT_NOTIFICATIONS = False

# Raw Ting notification eventType values the "Alerts" event entity can fire.
# Unknown types fire as "unknown" with the raw value in attributes.
NOTIFICATION_EVENT_TYPES = [
    "PowerOutage",
    "PowerOutageAndRestored",
    "PowerRestored",
    "Sag",
    "Swell",
    "WeatherAlert",
    "FireHazard",
    "FrozenPipe",
    "unknown",
]
POWER_OUTAGE_EVENT_TYPES = {"PowerOutage", "PowerOutageAndRestored", "PowerRestored"}
BROWNOUT_EVENT_TYPES = {"Sag", "Swell"}
WEATHER_EVENT_TYPES = {"WeatherAlert"}
# Notification types NOT posted as HA notifications (too noisy / covered elsewhere).
INSIGNIFICANT_NOTIFICATION_TYPES = {"Sag", "Swell", "WeatherAlert"}
