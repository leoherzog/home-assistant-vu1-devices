"""Constants for the VU1 Dials integration."""
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "vu1_dials"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_ADDON_MANAGED = "addon_managed"

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
DEFAULT_TIMEOUT = 10

# Platforms
PLATFORMS = ["sensor", "number", "light", "select", "button", "image"]

# Services
SERVICE_SET_DIAL_VALUE = "set_dial_value"
SERVICE_SET_DIAL_BACKLIGHT = "set_dial_backlight"
SERVICE_SET_DIAL_NAME = "set_dial_name"
SERVICE_SET_DIAL_IMAGE = "set_dial_image"
SERVICE_RELOAD_DIAL = "reload_dial"
SERVICE_CALIBRATE_DIAL = "calibrate_dial"

# Attributes
ATTR_VALUE = "value"
ATTR_RED = "red"
ATTR_GREEN = "green"
ATTR_BLUE = "blue"
ATTR_NAME = "name"
ATTR_MEDIA_CONTENT_ID = "media_content_id"

# Device info
MANUFACTURER = "Streacom"
MODEL = "VU1"

# Update modes
UPDATE_MODE_AUTOMATIC = "automatic"
UPDATE_MODE_MANUAL = "manual"

# Default dial configuration
DEFAULT_VALUE_MIN = 0
DEFAULT_VALUE_MAX = 100
DEFAULT_BACKLIGHT_COLOR = (100, 100, 100)  # White
DEFAULT_UPDATE_MODE = UPDATE_MODE_MANUAL


def get_dial_device_info(
    dial_uid: str,
    dial_data: dict[str, Any],
    server_device_identifier: str,
) -> DeviceInfo:
    """Return device info for a VU1 dial.

    Args:
        dial_uid: The unique identifier for the dial.
        dial_data: Dictionary containing dial information including dial_name.
        server_device_identifier: The identifier of the parent VU1 server device.

    Returns:
        DeviceInfo object for the dial device.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, dial_uid)},
        name=dial_data.get("dial_name", f"VU1 Dial {dial_uid}"),
        manufacturer=MANUFACTURER,
        model=MODEL,
        via_device=(DOMAIN, server_device_identifier),
    )


__all__ = [
    "DOMAIN",
    "CONF_HOST",
    "CONF_PORT",
    "CONF_API_KEY",
    "CONF_ADDON_MANAGED",
    "CONF_BOUND_ENTITY",
    "CONF_VALUE_MIN",
    "CONF_VALUE_MAX",
    "CONF_BACKLIGHT_COLOR",
    "CONF_DIAL_EASING",
    "CONF_BACKLIGHT_EASING",
    "CONF_UPDATE_MODE",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_UPDATE_INTERVAL",
    "DEFAULT_TIMEOUT",
    "PLATFORMS",
    "SERVICE_SET_DIAL_VALUE",
    "SERVICE_SET_DIAL_BACKLIGHT",
    "SERVICE_SET_DIAL_NAME",
    "SERVICE_SET_DIAL_IMAGE",
    "SERVICE_RELOAD_DIAL",
    "SERVICE_CALIBRATE_DIAL",
    "ATTR_VALUE",
    "ATTR_RED",
    "ATTR_GREEN",
    "ATTR_BLUE",
    "ATTR_NAME",
    "ATTR_MEDIA_CONTENT_ID",
    "MANUFACTURER",
    "MODEL",
    "UPDATE_MODE_AUTOMATIC",
    "UPDATE_MODE_MANUAL",
    "DEFAULT_VALUE_MIN",
    "DEFAULT_VALUE_MAX",
    "DEFAULT_BACKLIGHT_COLOR",
    "DEFAULT_UPDATE_MODE",
    "get_dial_device_info",
]