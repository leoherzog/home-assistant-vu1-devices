"""Constants for the VU1 Dials integration."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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

# Behavior presets matching the VU-Server web UI
BEHAVIOR_PRESETS = {
    "responsive": {
        "name": "Responsive",
        "dial_easing_period": 50,
        "dial_easing_step": 20,
        "backlight_easing_period": 50,
        "backlight_easing_step": 20,
        "description": "Dial is very responsive but may overshoot on large changes",
    },
    "balanced": {
        "name": "Balanced",
        "dial_easing_period": 50,
        "dial_easing_step": 5,
        "backlight_easing_period": 50,
        "backlight_easing_step": 10,
        "description": "Balance between responsive and smooth dial",
    },
    "smooth": {
        "name": "Smooth",
        "dial_easing_period": 50,
        "dial_easing_step": 1,
        "backlight_easing_period": 50,
        "backlight_easing_step": 5,
        "description": "Dial moves slowly with minimum overshoot",
    },
    "custom": {
        "name": "Custom",
        "description": "Manual configuration",
    },
}


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


class VU1DialEntity:
    """Mixin providing device_info for VU1 dial entities.

    Add this to any entity class that represents a per-dial entity.
    Requires the class to have ``_dial_uid`` and ``coordinator`` attributes
    (both provided by entity __init__ and CoordinatorEntity).
    """

    _dial_uid: str
    coordinator: DataUpdateCoordinator

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_data = (
            self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            if self.coordinator.data
            else {}
        )
        return get_dial_device_info(
            self._dial_uid, dial_data, self.coordinator.server_device_identifier
        )


def async_setup_dial_entities(
    coordinator: DataUpdateCoordinator,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    entity_factory: Callable[[str, dict[str, Any]], list[Entity]],
) -> None:
    """Set up dial entities and register callback for new dial discovery.

    This replaces the duplicated setup + callback pattern across all platform
    modules. Call it from each platform's ``async_setup_entry``.

    Args:
        coordinator: The VU1DataUpdateCoordinator instance.
        config_entry: The config entry being set up.
        async_add_entities: The callback to register new entities.
        entity_factory: A callable that takes (dial_uid, dial_info) and returns
            a list of entities to create for that dial.
    """
    entities: list[Entity] = []
    dial_data = coordinator.data.get("dials", {}) if coordinator.data else {}
    for dial_uid, dial_info in dial_data.items():
        entities.extend(entity_factory(dial_uid, dial_info))
    async_add_entities(entities)

    async def _async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
        """Create entities for newly discovered dials."""
        new_entities: list[Entity] = []
        for dial_uid, dial_info in new_dials.items():
            new_entities.extend(entity_factory(dial_uid, dial_info))
        if new_entities:
            async_add_entities(new_entities)

    unsub = coordinator.register_new_dial_callback(_async_add_new_dial_entities)
    config_entry.async_on_unload(unsub)
