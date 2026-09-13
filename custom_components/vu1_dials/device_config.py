"""Device configuration support for VU1 dials."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BACKLIGHT_COLOR,
    CONF_BOUND_ENTITY,
    CONF_UPDATE_MODE,
    CONF_VALUE_MAX,
    CONF_VALUE_MIN,
    DATA_CONFIG_MANAGER,
    DEFAULT_BACKLIGHT_COLOR,
    DEFAULT_UPDATE_MODE,
    DEFAULT_VALUE_MAX,
    DEFAULT_VALUE_MIN,
    DOMAIN,
    UPDATE_MODE_AUTOMATIC,
    UPDATE_MODE_MANUAL,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["VU1DialConfigManager", "async_get_config_manager"]

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_dial_configs"
SAVE_DELAY = 10


class VU1DialConfigManager:
    """Manage VU1 dial configurations with persistent storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the config manager."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        # In-memory cache of dial configurations: dial_uid -> config_dict
        self._configs: dict[str, dict[str, Any]] = {}
        # Event listeners for config changes: dial_uid -> [listener_functions]
        self._listeners: dict[str, list] = {}

    async def async_load(self) -> None:
        """Load configurations from storage."""
        if data := await self._store.async_load():
            self._configs = {
                dial_uid: self._validate_config(config)
                for dial_uid, config in data.get("dial_configs", {}).items()
            }

    @callback
    def _async_schedule_save(self) -> None:
        """Coalesce writes to storage."""
        self._store.async_delay_save(lambda: {"dial_configs": self._configs}, SAVE_DELAY)

    def get_dial_config(self, dial_uid: str) -> dict[str, Any]:
        """Get a copy of the configuration for a dial."""
        config = self._configs.get(dial_uid) or self._get_default_config()
        return {**config, CONF_BACKLIGHT_COLOR: list(config[CONF_BACKLIGHT_COLOR])}

    async def async_update_dial_config(
        self, dial_uid: str, config: dict[str, Any]
    ) -> None:
        """Merge and persist configuration for a dial."""
        self._configs[dial_uid] = self._validate_config(
            {**self.get_dial_config(dial_uid), **config}
        )
        self._async_schedule_save()
        await self._notify_listeners(dial_uid, self.get_dial_config(dial_uid))

    async def async_remove_dial_config(self, dial_uid: str) -> None:
        """Remove stored configuration for a dial.

        Safe to call even when the dial has no stored config (no-op). Intended
        for use from ``async_remove_config_entry_device`` so a removed dial
        doesn't leave orphaned persisted configuration behind.
        """
        if self._configs.pop(dial_uid, None) is not None:
            self._async_schedule_save()
        self._listeners.pop(dial_uid, None)

    async def async_remove(self) -> None:
        """Discard all stored dial configuration."""
        self._configs.clear()
        await self._store.async_remove()

    def _get_default_config(self) -> dict[str, Any]:
        """Get default dial configuration."""
        return {
            CONF_BOUND_ENTITY: None,
            CONF_VALUE_MIN: DEFAULT_VALUE_MIN,
            CONF_VALUE_MAX: DEFAULT_VALUE_MAX,
            CONF_BACKLIGHT_COLOR: list(DEFAULT_BACKLIGHT_COLOR),
            CONF_UPDATE_MODE: DEFAULT_UPDATE_MODE,
            "dial_easing_period": 50,
            "dial_easing_step": 5,
            "backlight_easing_period": 50,
            "backlight_easing_step": 5,
        }

    def _validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return a sanitized config holding only known keys."""
        defaults = self._get_default_config()
        validated = {key: config.get(key, default) for key, default in defaults.items()}

        for key in (CONF_VALUE_MIN, CONF_VALUE_MAX):
            try:
                validated[key] = float(validated[key])
            except (ValueError, TypeError):
                validated[key] = defaults[key]

        # Ensure min <= max (swap if necessary)
        if validated[CONF_VALUE_MIN] > validated[CONF_VALUE_MAX]:
            validated[CONF_VALUE_MIN], validated[CONF_VALUE_MAX] = validated[CONF_VALUE_MAX], validated[CONF_VALUE_MIN]

        # RGBW backlight, 0-100 per channel
        try:
            color = [max(0, min(100, int(c))) for c in validated[CONF_BACKLIGHT_COLOR]]
        except (ValueError, TypeError):
            color = []
        validated[CONF_BACKLIGHT_COLOR] = color if len(color) == 4 else defaults[CONF_BACKLIGHT_COLOR]

        if validated[CONF_UPDATE_MODE] not in (UPDATE_MODE_AUTOMATIC, UPDATE_MODE_MANUAL):
            validated[CONF_UPDATE_MODE] = defaults[CONF_UPDATE_MODE]

        return validated

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
            try:
                self._listeners[dial_uid].remove(listener)
            except ValueError:
                return  # Already removed
            if not self._listeners[dial_uid]:
                del self._listeners[dial_uid]

    async def _notify_listeners(self, dial_uid: str, config: dict[str, Any]) -> None:
        """Notify listeners of configuration changes."""
        for listener in list(self._listeners.get(dial_uid, [])):
            try:
                await listener(dial_uid, config)
            except Exception:
                _LOGGER.exception("Error notifying config listener")


@callback
def async_get_config_manager(hass: HomeAssistant) -> VU1DialConfigManager:
    """Return the dial configuration manager loaded in async_setup."""
    return hass.data[DATA_CONFIG_MANAGER]
