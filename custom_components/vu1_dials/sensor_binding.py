"""Sensor binding system for VU1 dials."""
import functools
import logging
import re
from typing import Any, Callable

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer

from .const import (
    DOMAIN,
    CONF_BOUND_ENTITY,
    CONF_VALUE_MIN,
    CONF_VALUE_MAX,
    CONF_BACKLIGHT_COLOR,
    CONF_UPDATE_MODE,
    UPDATE_MODE_AUTOMATIC,
)
from .device_config import async_get_config_manager
from .vu1_api import VU1APIClient, VU1APIError

_LOGGER = logging.getLogger(__name__)

__all__ = ["VU1SensorBindingManager", "async_get_binding_manager"]

# Debounce settings
DEBOUNCE_SECONDS = 5  # Minimum seconds between API calls per dial


class VU1SensorBindingManager:
    """Manage sensor bindings for VU1 dials."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the binding manager."""
        self.hass = hass
        # Track active bindings: dial_uid -> {entity_id, config, dial_data, client, last_state}
        self._bindings: dict[str, dict[str, Any]] = {}
        # Track state change listeners with reference counting:
        # entity_id -> {"unsub": unsubscribe_callable, "count": number_of_dials_using_it}
        self._listeners: dict[str, dict[str, Any]] = {}
        self._config_manager = async_get_config_manager(hass)
        # Debounce API calls to prevent rapid updates: dial_uid -> debouncer
        self._debouncers: dict[str, Debouncer] = {}

    async def async_setup(self) -> None:
        """Set up the binding manager."""
        await self._config_manager.async_load()

    async def async_update_bindings(self, coordinator_data: dict[str, Any]) -> None:
        """Update bindings based on current dial configurations."""
        # Clean up old bindings for dials that no longer exist
        dial_data = coordinator_data.get("dials", {})
        existing_dials = set(dial_data.keys())
        old_dials = set(self._bindings.keys()) - existing_dials
        
        for dial_uid in old_dials:
            await self._remove_binding(dial_uid)

        # Update bindings for current dials - checks config and creates/updates as needed
        for dial_uid in existing_dials:
            config = self._config_manager.get_dial_config(dial_uid)
            await self._update_binding(dial_uid, config, dial_data[dial_uid])

    async def _update_binding(
        self, dial_uid: str, config: dict[str, Any], dial_data: dict[str, Any]
    ) -> None:
        """Update binding for a specific dial."""
        bound_entity = config.get(CONF_BOUND_ENTITY)
        update_mode = config.get(CONF_UPDATE_MODE)

        existing_binding = self._bindings.get(dial_uid)

        # If mode is not automatic, or no entity is bound, remove any existing binding
        if update_mode != UPDATE_MODE_AUTOMATIC or not bound_entity:
            if existing_binding:
                await self._remove_binding(dial_uid)
            return

        # At this point, mode is automatic and an entity is bound
        if existing_binding:
            # Check if the bound entity has changed - if so, recreate the binding
            if existing_binding.get("entity_id") != bound_entity:
                await self._remove_binding(dial_uid)
                await self._create_binding(dial_uid, bound_entity, config, dial_data)
            else:
                # Same entity - just update the config (range settings, colors, etc.)
                existing_binding["config"] = config.copy()
                existing_binding["dial_data"] = dial_data.copy()
        else:
            # No binding exists for this dial - create one
            await self._create_binding(dial_uid, bound_entity, config, dial_data)

    async def _create_binding(
        self,
        dial_uid: str,
        entity_id: str,
        config: dict[str, Any],
        dial_data: dict[str, Any],
    ) -> None:
        """Create a new sensor binding."""
        # Validate entity exists
        entity_registry = er.async_get(self.hass)
        if not entity_registry.async_get(entity_id) and not self.hass.states.get(entity_id):
            _LOGGER.warning("Bound entity %s does not exist for dial %s", entity_id, dial_uid)
            return

        # Get client for this dial
        client = self._get_client_for_dial(dial_uid)
        if not client:
            _LOGGER.debug("No client found for dial %s (integration may still be loading)", dial_uid)
            return

        # Clean up any existing debouncer to prevent memory leaks
        if existing_debouncer := self._debouncers.get(dial_uid):
            _LOGGER.debug("Cleaning up existing debouncer for dial %s", dial_uid)
            existing_debouncer.async_cancel()
            del self._debouncers[dial_uid]

        # Store binding info
        self._bindings[dial_uid] = {
            "entity_id": entity_id,
            "config": config.copy(),
            "dial_data": dial_data.copy(),
            "client": client,
            "last_state": None,  # Store the most recent state for debounced processing
        }

        # Create debouncer to limit API calls (5 second cooldown per dial)
        self._debouncers[dial_uid] = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=DEBOUNCE_SECONDS,
            immediate=False,
            # Bind the dial_uid to the function using partial
            function=functools.partial(self._apply_sensor_value, dial_uid),
        )

        # Set up state change listener with reference counting
        # Only create a new listener if this is the first dial binding to this entity
        if entity_id in self._listeners:
            # Increment reference count for existing listener
            self._listeners[entity_id]["count"] += 1
            _LOGGER.debug(
                "Reusing existing listener for %s (count: %d)",
                entity_id, self._listeners[entity_id]["count"]
            )
        else:
            # Create new listener for this entity
            unsub = async_track_state_change_event(
                self.hass, [entity_id], self._async_sensor_state_changed
            )
            self._listeners[entity_id] = {"unsub": unsub, "count": 1}
            _LOGGER.debug("Created new listener for %s", entity_id)

        _LOGGER.info("Created sensor binding: %s -> dial %s", entity_id, dial_uid)

        # Apply initial state immediately (bypass debouncer for first update)
        initial_state = self.hass.states.get(entity_id)
        if initial_state:
            await self._apply_sensor_value_from_state(dial_uid, initial_state)

    async def _remove_binding(self, dial_uid: str) -> None:
        """Remove a sensor binding."""
        if dial_uid not in self._bindings:
            return

        binding_info = self._bindings[dial_uid]
        entity_id = binding_info["entity_id"]

        # Decrement reference count for the listener
        # Only remove the listener when no more dials are using it
        if entity_id in self._listeners:
            self._listeners[entity_id]["count"] -= 1
            if self._listeners[entity_id]["count"] <= 0:
                # Last dial using this entity - unsubscribe and remove
                self._listeners[entity_id]["unsub"]()
                del self._listeners[entity_id]
                _LOGGER.debug("Removed listener for %s (no more dials bound)", entity_id)
            else:
                _LOGGER.debug(
                    "Decremented listener count for %s (count: %d)",
                    entity_id, self._listeners[entity_id]["count"]
                )

        # Cancel and remove debouncer
        if debouncer := self._debouncers.pop(dial_uid, None):
            debouncer.async_cancel()

        # Remove binding
        del self._bindings[dial_uid]
        _LOGGER.info("Removed sensor binding for dial %s", dial_uid)

    @callback
    def _async_sensor_state_changed(self, event) -> None:
        """Handle sensor state change."""
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        
        if not new_state:
            return

        # Find dial(s) bound to this entity
        for dial_uid, binding_info in self._bindings.items():
            if binding_info["entity_id"] == entity_id:
                # Store the latest state for debounced processing
                binding_info["last_state"] = new_state
                # Schedule the API call (debouncer will prevent rapid updates)
                if debouncer := self._debouncers.get(dial_uid):
                    debouncer.async_schedule_call()

    async def _apply_sensor_value(self, dial_uid: str) -> None:
        """Apply the last known sensor value to the dial. Called by the debouncer."""
        binding_info = self._bindings.get(dial_uid)
        if not binding_info or not binding_info["last_state"]:
            return
            
        await self._apply_sensor_value_from_state(dial_uid, binding_info["last_state"])

    async def _apply_sensor_value_from_state(self, dial_uid: str, state: State) -> None:
        """Core logic for applying a sensor value to a dial."""
        binding_info = self._bindings.get(dial_uid)
        if not binding_info:
            return

        config = binding_info["config"]
        client = binding_info["client"]
        
        try:
            # Parse numeric value from sensor state
            sensor_value = self._parse_sensor_value(state)
            if sensor_value is None:
                return

            # Map sensor range to dial 0-100% range
            dial_value = self._map_value_to_dial(sensor_value, config)
            
            # Update dial position
            await client.set_dial_value(dial_uid, dial_value)
            
            # Apply saved backlight color if configured
            backlight_color = config.get(CONF_BACKLIGHT_COLOR)
            if backlight_color:
                await client.set_dial_backlight(
                    dial_uid, backlight_color[0], backlight_color[1], backlight_color[2]
                )
            
            _LOGGER.debug(
                "Applied sensor value %s -> dial %s (value: %s)",
                sensor_value, dial_uid, dial_value
            )

        except VU1APIError as err:
            _LOGGER.error("Failed to update dial %s from sensor: %s", dial_uid, err)
        except Exception as err:
            _LOGGER.exception("Unexpected error updating dial %s from sensor: %s", dial_uid, err)

    def _parse_sensor_value(self, state: State) -> float | None:
        """Parse sensor state to numeric value."""
        try:
            # Try direct numeric conversion
            return float(state.state)
        except (ValueError, TypeError):
            # Handle special Home Assistant states
            if state.state in ["unknown", "unavailable", "none"]:
                return None
            
            # Extract first numeric value from string (e.g., "23.5°C" -> 23.5)
            match = re.search(r'[-+]?\d*\.?\d+', str(state.state))
            if match:
                return float(match.group())
            
            return None

    def _map_value_to_dial(self, sensor_value: float, config: dict[str, Any]) -> int:
        """Map sensor value to dial range (0-100)."""
        value_min = config.get(CONF_VALUE_MIN, 0)
        value_max = config.get(CONF_VALUE_MAX, 100)
        
        # Handle edge case where min equals max
        if value_min == value_max:
            return 50  # Middle value if no range defined
        
        # Clamp and map sensor value to 0-100 range
        if sensor_value <= value_min:
            dial_value = 0
        elif sensor_value >= value_max:
            dial_value = 100
        else:
            # Linear interpolation between min and max
            dial_value = int(((sensor_value - value_min) / (value_max - value_min)) * 100)
        
        return max(0, min(100, dial_value))

    def _get_client_for_dial(self, dial_uid: str) -> VU1APIClient | None:
        """Get VU1 API client for a specific dial."""
        # Find the config entry that contains this dial UID using runtime_data
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
                continue
            coordinator = entry.runtime_data.coordinator
            if coordinator.data and dial_uid in coordinator.data.get("dials", {}):
                return entry.runtime_data.client
        return None

    async def async_reconfigure_dial_binding(self, dial_uid: str) -> None:
        """Reconfigure binding for a specific dial after configuration changes.

        This is the public method that should be called when a dial's configuration
        has been updated and the binding needs to be refreshed.
        """
        # Get the updated configuration
        config = self._config_manager.get_dial_config(dial_uid)

        # Find the dial data from the coordinator using runtime_data
        dial_data = None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
                continue
            coordinator = entry.runtime_data.coordinator
            if coordinator.data and dial_uid in coordinator.data.get("dials", {}):
                dial_data = coordinator.data["dials"][dial_uid]
                break

        if dial_data is None:
            _LOGGER.warning("Could not find dial data for %s during reconfiguration", dial_uid)
            return

        # Update the binding using our private method
        await self._update_binding(dial_uid, config, dial_data)
        _LOGGER.info("Reconfigured binding for dial %s", dial_uid)

    async def async_shutdown(self) -> None:
        """Shutdown the binding manager."""
        # Cancel all debouncers
        for debouncer in self._debouncers.values():
            if debouncer:
                debouncer.async_cancel()
        
        # Remove all bindings
        dial_uids = list(self._bindings.keys())
        for dial_uid in dial_uids:
            await self._remove_binding(dial_uid)


@callback
def async_get_binding_manager(hass: HomeAssistant) -> VU1SensorBindingManager:
    """Get the sensor binding manager."""
    if f"{DOMAIN}_binding_manager" not in hass.data:
        hass.data[f"{DOMAIN}_binding_manager"] = VU1SensorBindingManager(hass)
    return hass.data[f"{DOMAIN}_binding_manager"]
