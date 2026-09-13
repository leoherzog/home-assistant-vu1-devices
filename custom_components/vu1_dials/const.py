"""Constants for the VU1 Dials integration."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.util.hass_dict import HassKey

if TYPE_CHECKING:
    from .device_config import VU1DialConfigManager
    from .sensor_binding import VU1SensorBindingManager

DOMAIN = "vu1_dials"

DATA_CONFIG_MANAGER: HassKey[VU1DialConfigManager] = HassKey(f"{DOMAIN}_config_manager")
DATA_BINDING_MANAGER: HassKey[VU1SensorBindingManager] = HassKey(f"{DOMAIN}_binding_manager")

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
DEFAULT_UPDATE_INTERVAL = 30

# Platforms
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.LIGHT, Platform.SELECT, Platform.BUTTON, Platform.IMAGE]

# Services
SERVICE_SET_DIAL_VALUE = "set_dial_value"
SERVICE_SET_DIAL_BACKLIGHT = "set_dial_backlight"
SERVICE_SET_DIAL_NAME = "set_dial_name"
SERVICE_SET_DIAL_IMAGE = "set_dial_image"
SERVICE_RELOAD_DIAL = "reload_dial"

# Attributes
ATTR_VALUE = "value"
ATTR_RED = "red"
ATTR_GREEN = "green"
ATTR_BLUE = "blue"
ATTR_WHITE = "white"
ATTR_NAME = "name"
ATTR_MEDIA_CONTENT_ID = "media_content_id"

# Device info
MANUFACTURER = "Streacom"
MODEL = "VU1"

# Update modes
UPDATE_MODE_AUTOMATIC = "automatic"
UPDATE_MODE_MANUAL = "manual"

# Backlight channel order used by the server payload and the stored RGBW list
BACKLIGHT_CHANNELS = ("red", "green", "blue", "white")

# Default dial configuration
DEFAULT_VALUE_MIN = 0
DEFAULT_VALUE_MAX = 100
DEFAULT_BACKLIGHT_COLOR = (0, 0, 0, 0)
DEFAULT_UPDATE_MODE = UPDATE_MODE_MANUAL

# Behavior presets matching the VU-Server web UI
BEHAVIOR_PRESETS = {
    "responsive": {
        "dial_easing_period": 50,
        "dial_easing_step": 20,
        "backlight_easing_period": 50,
        "backlight_easing_step": 20,
        "description": "Dial is very responsive but may overshoot on large changes",
    },
    "balanced": {
        "dial_easing_period": 50,
        "dial_easing_step": 5,
        "backlight_easing_period": 50,
        "backlight_easing_step": 10,
        "description": "Balance between responsive and smooth dial",
    },
    "smooth": {
        "dial_easing_period": 50,
        "dial_easing_step": 1,
        "backlight_easing_period": 50,
        "backlight_easing_step": 5,
        "description": "Dial moves slowly with minimum overshoot",
    },
}
