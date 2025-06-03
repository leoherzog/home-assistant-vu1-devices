"""Device configuration support for VU1 dials."""
import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

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
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_dial_configs"


class VU1DialConfigManager:
    """Manage VU1 dial configurations."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the config manager."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._configs: Dict[str, Dict[str, Any]] = {}
        self._listeners: Dict[str, list] = {}

    async def async_load(self) -> None:
        """Load configurations from storage."""
        data = await self._store.async_load()
        if data:
            self._configs = data.get("dial_configs", {})

    async def async_save(self) -> None:
        """Save configurations to storage."""
        await self._store.async_save({"dial_configs": self._configs})

    def get_dial_config(self, dial_uid: str) -> Dict[str, Any]:
        """Get configuration for a dial."""
        return self._configs.get(dial_uid, self._get_default_config())

    async def async_update_dial_config(
        self, dial_uid: str, config: Dict[str, Any]
    ) -> None:
        """Update configuration for a dial."""
        # Validate the configuration
        validated_config = self._validate_config(config)
        
        # Store the configuration
        self._configs[dial_uid] = validated_config
        await self.async_save()
        
        # Notify listeners
        await self._notify_listeners(dial_uid, validated_config)

    def _get_default_config(self) -> Dict[str, Any]:
        """Get default dial configuration."""
        return {
            CONF_BOUND_ENTITY: None,
            CONF_VALUE_MIN: DEFAULT_VALUE_MIN,
            CONF_VALUE_MAX: DEFAULT_VALUE_MAX,
            CONF_BACKLIGHT_COLOR: DEFAULT_BACKLIGHT_COLOR.copy(),
            CONF_DIAL_EASING: "linear",
            CONF_BACKLIGHT_EASING: "linear",
            CONF_UPDATE_MODE: DEFAULT_UPDATE_MODE,
            "dial_easing_period": 50,
            "dial_easing_step": 5,
            "backlight_easing_period": 50,
            "backlight_easing_step": 5,
        }

    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and sanitize dial configuration."""
        validated = self._get_default_config()
        
        # Update with provided values, validating each
        if CONF_BOUND_ENTITY in config:
            entity_id = config[CONF_BOUND_ENTITY]
            if entity_id and self._is_valid_entity(entity_id):
                validated[CONF_BOUND_ENTITY] = entity_id
        
        if CONF_VALUE_MIN in config:
            try:
                validated[CONF_VALUE_MIN] = max(0, min(100, float(config[CONF_VALUE_MIN])))
            except (ValueError, TypeError):
                pass
        
        if CONF_VALUE_MAX in config:
            try:
                validated[CONF_VALUE_MAX] = max(0, min(100, float(config[CONF_VALUE_MAX])))
            except (ValueError, TypeError):
                pass
        
        # Ensure min <= max
        if validated[CONF_VALUE_MIN] > validated[CONF_VALUE_MAX]:
            validated[CONF_VALUE_MIN], validated[CONF_VALUE_MAX] = validated[CONF_VALUE_MAX], validated[CONF_VALUE_MIN]
        
        if CONF_BACKLIGHT_COLOR in config:
            color = config[CONF_BACKLIGHT_COLOR]
            if isinstance(color, list) and len(color) == 3:
                try:
                    validated[CONF_BACKLIGHT_COLOR] = [
                        max(0, min(100, int(c))) for c in color
                    ]
                except (ValueError, TypeError):
                    pass
        
        if CONF_UPDATE_MODE in config:
            mode = config[CONF_UPDATE_MODE]
            if mode in [UPDATE_MODE_AUTOMATIC, "manual"]:
                validated[CONF_UPDATE_MODE] = mode
        
        return validated

    def _is_valid_entity(self, entity_id: str) -> bool:
        """Check if entity ID is valid and exists."""
        if not entity_id:
            return False
        
        entity_registry = er.async_get(self.hass)
        return entity_registry.async_get(entity_id) is not None

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

    async def _notify_listeners(self, dial_uid: str, config: Dict[str, Any]) -> None:
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