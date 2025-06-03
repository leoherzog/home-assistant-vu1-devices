"""Constants for the VU1 Devices integration."""

DOMAIN = "vu1_devices"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"

# Dial configuration keys
CONF_BOUND_ENTITY = "bound_entity"
CONF_VALUE_MIN = "value_min"
CONF_VALUE_MAX = "value_max"
CONF_BACKLIGHT_COLOR = "backlight_color"
CONF_DIAL_EASING = "dial_easing"
CONF_BACKLIGHT_EASING = "backlight_easing"
CONF_UPDATE_MODE = "update_mode"

# Default values
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5340
DEFAULT_UPDATE_INTERVAL = 30

# Platforms
PLATFORMS = ["sensor", "number", "select", "switch", "text"]

# Services
SERVICE_SET_DIAL_VALUE = "set_dial_value"
SERVICE_SET_DIAL_BACKLIGHT = "set_dial_backlight"
SERVICE_SET_DIAL_NAME = "set_dial_name"
SERVICE_RELOAD_DIAL = "reload_dial"
SERVICE_CALIBRATE_DIAL = "calibrate_dial"

# Attributes
ATTR_DIAL_UID = "dial_uid"
ATTR_VALUE = "value"
ATTR_RED = "red"
ATTR_GREEN = "green"
ATTR_BLUE = "blue"
ATTR_NAME = "name"

# Device info
MANUFACTURER = "Streacom"
MODEL = "VU1"

# Update modes
UPDATE_MODE_AUTOMATIC = "automatic"
UPDATE_MODE_MANUAL = "manual"

# Default dial configuration
DEFAULT_VALUE_MIN = 0
DEFAULT_VALUE_MAX = 100
DEFAULT_BACKLIGHT_COLOR = [100, 100, 100]  # White
DEFAULT_UPDATE_MODE = UPDATE_MODE_MANUAL