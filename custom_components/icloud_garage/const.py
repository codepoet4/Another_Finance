"""Constants for iCloud Garage Automation."""

DOMAIN = "icloud_garage"

# Config entry keys
CONF_MY_DEVICE = "my_device_tracker"
CONF_WIFE_DEVICE = "wife_device_tracker"
CONF_GARAGE_DOOR = "garage_door_entity"
CONF_MOTION_SENSOR = "motion_sensor_entity"
CONF_NOTIFICATION_TARGET = "notification_target"
CONF_AVG_SPEED_KPH = "avg_speed_kph"
CONF_HOME_ZONE = "home_zone"
# Optional overrides for per-phone location-request notify services.
# If omitted the service is derived from the device_tracker entity name:
#   device_tracker.rpip  →  notify.mobile_app_rpip
CONF_MY_NOTIFY = "my_notify_service"
CONF_WIFE_NOTIFY = "wife_notify_service"

# Defaults
DEFAULT_AVG_SPEED_KPH = 40.0  # kph assumed average driving speed
DEFAULT_HOME_ZONE = "zone.home"

# Polling bounds
MIN_POLL_INTERVAL_S = 30       # seconds — never poll faster than this
MAX_POLL_INTERVAL_S = 1800     # seconds — never wait more than 30 min

# Location update request
LOCATION_UPDATE_WAIT_S = 20   # seconds to wait after sending request_location_update
                               # before reading the (hopefully refreshed) device_tracker

# Arrival detection
ARRIVAL_PROXIMITY_M = 20       # metres — trigger window around house
ACCURACY_REQUIRED_M = 10       # metres — GPS accuracy must be this good

# Motion correlation window
MOTION_BEFORE_S = 5            # seconds before proximity detection
MOTION_AFTER_S = 10            # seconds after proximity detection

# Garage safety
GARAGE_COOLDOWN_S = 600        # 10 minutes after close before re-opening

# Driving detection
DRIVING_SPEED_THRESHOLD_KPH = 8.0  # kph — below this is walking / stationary

# Internal state machine values
STATE_HOME = "home"
STATE_AWAY = "away"
STATE_DRIVING = "driving"
STATE_APPROACHING = "approaching"
