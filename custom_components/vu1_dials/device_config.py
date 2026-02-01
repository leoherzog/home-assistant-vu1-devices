"""Device configuration support for VU1 dials."""
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    CONF_BOUND_ENTITY,
    CONF_VALUE_MIN,
    CONF_VALUE_MAX,
    CONF_BACKLIGHT_COLOR,
    CONF_DIAL_EASING,
    CONF_BACKLIGHT_EASING,
    CONF_UPDATE_MODE,
    DEFAULT_VALUE_MIN,
    DEFAULT_VALUE_MAX,
    DEFAULT_BACKLIGHT_COLOR,
    DEFAULT_UPDATE_MODE,
    UPDATE_MODE_AUTOMATIC,
    UPDATE_MODE_MANUAL,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["VU1DialConfigManager", "async_get_config_manager"]

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_dial_configs"


class VU1DialConfigManager:
    """Manage VU1 dial configurations with persistent storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the config manager."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # In-memory cache of dial configurations: dial_uid -> config_dict
        self._configs: dict[str, dict[str, Any]] = {}
        # Event listeners for config changes: dial_uid -> [listener_functions]
        self._listeners: dict[str, list] = {}

    async def async_load(self) -> None:
        """Load configurations from storage."""
        data = await self._store.async_load()
        if data:
            self._configs = data.get("dial_configs", {})

    async def async_save(self) -> None:
        """Save configurations to storage."""
        await self._store.async_save({"dial_configs": self._configs})

    def get_dial_config(self, dial_uid: str) -> dict[str, Any]:
        """Get configuration for a dial."""
        return self._configs.get(dial_uid, self._get_default_config())

    async def async_update_dial_config(
        self, dial_uid: str, config: dict[str, Any]
    ) -> None:
        """Update configuration for a dial."""
        # Get the existing configuration (includes defaults)
        existing_config = self.get_dial_config(dial_uid)
        
        # Merge new settings with existing config
        merged_config = {**existing_config, **config}
        
        # Validate and sanitize the merged configuration
        validated_config = self._validate_config(merged_config)
        
        # Store in memory cache and persist to disk
        self._configs[dial_uid] = validated_config
        await self.async_save()
        
        # Notify listeners (entities, binding manager) of changes
        await self._notify_listeners(dial_uid, validated_config)

    def _get_default_config(self) -> dict[str, Any]:
        """Get default dial configuration."""
        return {
            CONF_BOUND_ENTITY: None,
            CONF_VALUE_MIN: DEFAULT_VALUE_MIN,
            CONF_VALUE_MAX: DEFAULT_VALUE_MAX,
            CONF_BACKLIGHT_COLOR: list(DEFAULT_BACKLIGHT_COLOR),  # Convert tuple to list for storage
            CONF_DIAL_EASING: "linear",
            CONF_BACKLIGHT_EASING: "linear",
            CONF_UPDATE_MODE: DEFAULT_UPDATE_MODE,
            "dial_easing_period": 50,
            "dial_easing_step": 5,
            "backlight_easing_period": 50,
            "backlight_easing_step": 5,
        }

    def _validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Validate and sanitize dial configuration."""
        # Create a copy to operate on, preserving the original
        validated = config.copy()
        
        # Fill in any missing keys with defaults
        defaults = self._get_default_config()
        for key, default_value in defaults.items():
            if key not in validated:
                validated[key] = default_value
        
        # Validate bound entity exists in entity registry
        if validated.get(CONF_BOUND_ENTITY) and not self._is_valid_entity(validated[CONF_BOUND_ENTITY]):
            validated[CONF_BOUND_ENTITY] = None
        
        # Validate value_min as float
        try:
            validated[CONF_VALUE_MIN] = float(validated[CONF_VALUE_MIN])
        except (ValueError, TypeError, KeyError):
            validated[CONF_VALUE_MIN] = defaults[CONF_VALUE_MIN]

        # Validate value_max as float
        try:
            validated[CONF_VALUE_MAX] = float(validated[CONF_VALUE_MAX])
        except (ValueError, TypeError, KeyError):
            validated[CONF_VALUE_MAX] = defaults[CONF_VALUE_MAX]
            
        # Ensure min <= max (swap if necessary)
        if validated[CONF_VALUE_MIN] > validated[CONF_VALUE_MAX]:
            validated[CONF_VALUE_MIN], validated[CONF_VALUE_MAX] = validated[CONF_VALUE_MAX], validated[CONF_VALUE_MIN]
        
        # Validate backlight_color as RGB values (0-100 each)
        color = validated.get(CONF_BACKLIGHT_COLOR)
        if isinstance(color, (list, tuple)) and len(color) == 3:
            try:
                # Clamp RGB values to 0-100 range and store as list for JSON compatibility
                validated[CONF_BACKLIGHT_COLOR] = [max(0, min(100, int(c))) for c in color]
            except (ValueError, TypeError):
                validated[CONF_BACKLIGHT_COLOR] = list(defaults[CONF_BACKLIGHT_COLOR])
        else:
            validated[CONF_BACKLIGHT_COLOR] = list(defaults[CONF_BACKLIGHT_COLOR])

        # Validate update_mode is one of the allowed values
        if validated.get(CONF_UPDATE_MODE) not in [UPDATE_MODE_AUTOMATIC, UPDATE_MODE_MANUAL]:
            validated[CONF_UPDATE_MODE] = defaults[CONF_UPDATE_MODE]

        return validated

    def _is_valid_entity(self, entity_id: str) -> bool:
        """Check if entity ID is valid and exists."""
        if not entity_id:
            return False
        
        entity_registry = er.async_get(self.hass)
        if entity_registry.async_get(entity_id) is not None:
            return True

        return self.hass.states.get(entity_id) is not None

    @callback
    def async_add_listener(self, dial_uid: str, listener) -> None:
        """Add a listener for dial configuration changes."""
        if dial_uid not in self._listeners:
            self._listeners[dial_uid] = []
        self._listeners[dial_uid].append(listener)

    @callback
    def async_remove_listener(self, dial_uid: str, listener) -> None:
        """Remove a listener for dial configuration changes."""
        if dial_uid in self._listeners:
            self._listeners[dial_uid].remove(listener)
            if not self._listeners[dial_uid]:
                del self._listeners[dial_uid]

    async def _notify_listeners(self, dial_uid: str, config: dict[str, Any]) -> None:
        """Notify listeners of configuration changes."""
        if dial_uid in self._listeners:
            for listener in self._listeners[dial_uid]:
                try:
                    await listener(dial_uid, config)
                except Exception as err:
                    _LOGGER.exception("Error notifying config listener: %s", err)


@callback
def async_get_config_manager(hass: HomeAssistant) -> VU1DialConfigManager:
    """Get the dial configuration manager."""
    if f"{DOMAIN}_config_manager" not in hass.data:
        hass.data[f"{DOMAIN}_config_manager"] = VU1DialConfigManager(hass)
    return hass.data[f"{DOMAIN}_config_manager"]
