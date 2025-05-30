"""Constants for the VU1 Devices integration."""

DOMAIN = "vu1_devices"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"

# Default values
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5340
DEFAULT_UPDATE_INTERVAL = 30

# Platforms
PLATFORMS = ["sensor"]

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