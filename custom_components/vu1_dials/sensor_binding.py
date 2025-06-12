"""Sensor binding system for VU1 dials."""
import asyncio
import functools
import logging
from typing import Any, Dict, Optional

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

# Debounce settings
DEBOUNCE_SECONDS = 5  # Minimum seconds between API calls per dial


class VU1SensorBindingManager:
    """Manage sensor bindings for VU1 dials."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the binding manager."""
        self.hass = hass
        self._bindings: Dict[str, Dict[str, Any]] = {}  # dial_uid -> binding_info
        self._listeners: Dict[str, Any] = {}  # entity_id -> listener
        self._config_manager = async_get_config_manager(hass)
        self._debouncers: Dict[str, Debouncer] = {}  # dial_uid -> debouncer

    async def async_setup(self) -> None:
        """Set up the binding manager."""
        await self._config_manager.async_load()

    async def async_update_bindings(self, coordinator_data: Dict[str, Any]) -> None:
        """Update bindings based on current dial configurations."""
        # Clean up old bindings for dials that no longer exist
        dial_data = coordinator_data.get("dials", {})
        existing_dials = set(dial_data.keys())
        old_dials = set(self._bindings.keys()) - existing_dials
        
        for dial_uid in old_dials:
            await self._remove_binding(dial_uid)

        # Update bindings for current dials
        for dial_uid in existing_dials:
            config = self._config_manager.get_dial_config(dial_uid)
            await self._update_binding(dial_uid, config, dial_data[dial_uid])

    async def _update_binding(
        self, dial_uid: str, config: Dict[str, Any], dial_data: Dict[str, Any]
    ) -> None:
        """Update binding for a specific dial."""
        bound_entity = config.get(CONF_BOUND_ENTITY)
        update_mode = config.get(CONF_UPDATE_MODE)

        existing_binding = self._bindings.get(dial_uid)

        # If mode is not automatic, or no entity is bound, remove any existing binding.
        if update_mode != UPDATE_MODE_AUTOMATIC or not bound_entity:
            if existing_binding:
                await self._remove_binding(dial_uid)
            return

        # At this point, mode is automatic and an entity is bound.
        if existing_binding:
            # A binding already exists. Check if it's for the same entity.
            if existing_binding.get("entity_id") != bound_entity:
                # The bound entity has changed. Recreate the binding.
                await self._remove_binding(dial_uid)
                await self._create_binding(dial_uid, bound_entity, config, dial_data)
            else:
                # The binding is for the same entity. Just update the config in memory.
                existing_binding["config"] = config.copy()
                existing_binding["dial_data"] = dial_data.copy()
        else:
            # No binding exists for this dial. Create one.
            await self._create_binding(dial_uid, bound_entity, config, dial_data)

    async def _create_binding(
        self,
        dial_uid: str,
        entity_id: str,
        config: Dict[str, Any],
        dial_data: Dict[str, Any],
    ) -> None:
        """Create a new sensor binding."""
        # Validate entity exists
        entity_registry = er.async_get(self.hass)
        if not entity_registry.async_get(entity_id):
            _LOGGER.warning("Bound entity %s does not exist for dial %s", entity_id, dial_uid)
            return

        # Get client for this dial
        client = self._get_client_for_dial(dial_uid)
        if not client:
            _LOGGER.debug("No client found for dial %s (integration may still be loading)", dial_uid)
            return

        # Store binding info
        self._bindings[dial_uid] = {
            "entity_id": entity_id,
            "config": config.copy(),
            "dial_data": dial_data.copy(),
            "client": client,
            "last_state": None,  # Add a place to store the most recent state
        }

        # Create debouncer that calls _apply_sensor_value for this specific dial
        self._debouncers[dial_uid] = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=DEBOUNCE_SECONDS,
            immediate=False,
            # Use functools.partial here to create a stable reference to the function
            function=functools.partial(self._apply_sensor_value, dial_uid),
        )

        # Set up state change listener
        self._listeners[entity_id] = async_track_state_change_event(
            self.hass, [entity_id], self._async_sensor_state_changed
        )

        _LOGGER.info("Created sensor binding: %s -> dial %s", entity_id, dial_uid)

        # Apply initial state immediately, bypassing the debouncer
        initial_state = self.hass.states.get(entity_id)
        if initial_state:
            # We can now pass the state directly since we are not using the debouncer here
            await self._apply_sensor_value_from_state(dial_uid, initial_state)

    async def _remove_binding(self, dial_uid: str) -> None:
        """Remove a sensor binding."""
        if dial_uid not in self._bindings:
            return

        binding_info = self._bindings[dial_uid]
        entity_id = binding_info["entity_id"]

        # Remove state change listener
        if entity_id in self._listeners:
            self._listeners[entity_id]()
            del self._listeners[entity_id]

        # Cancel and remove debouncer
        if debouncer := self._debouncers.pop(dial_uid, None):
            await debouncer.async_shutdown()

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
                # 1. Store the latest state
                binding_info["last_state"] = new_state
                # 2. Schedule the call. The debouncer will execute the function we gave it earlier.
                if debouncer := self._debouncers.get(dial_uid):
                    debouncer.async_schedule_call()

    async def _apply_sensor_value(self, dial_uid: str) -> None:
        """Apply the last known sensor value to the dial. Called by the debouncer."""
        binding_info = self._bindings.get(dial_uid)
        if not binding_info or not binding_info["last_state"]:
            return
            
        await self._apply_sensor_value_from_state(dial_uid, binding_info["last_state"])

    async def _apply_sensor_value_from_state(self, dial_uid: str, state: State) -> None:
        """The core logic for applying a sensor value to a dial."""
        binding_info = self._bindings.get(dial_uid)
        if not binding_info:
            return

        config = binding_info["config"]
        client = binding_info["client"]
        
        try:
            # Parse sensor value
            sensor_value = self._parse_sensor_value(state)
            if sensor_value is None:
                return

            # Map to dial range
            dial_value = self._map_value_to_dial(sensor_value, config)
            
            # Update dial
            await client.set_dial_value(dial_uid, dial_value)
            
            # Update backlight if configured
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

    def _parse_sensor_value(self, state: State) -> Optional[float]:
        """Parse sensor state to numeric value."""
        try:
            # Try direct numeric conversion
            return float(state.state)
        except (ValueError, TypeError):
            # Handle special states
            if state.state in ["unknown", "unavailable", "none"]:
                return None
            
            # Try to extract numeric value from string
            import re
            match = re.search(r'[-+]?\d*\.?\d+', str(state.state))
            if match:
                return float(match.group())
            
            return None

    def _map_value_to_dial(self, sensor_value: float, config: Dict[str, Any]) -> int:
        """Map sensor value to dial range (0-100)."""
        value_min = config.get(CONF_VALUE_MIN, 0)
        value_max = config.get(CONF_VALUE_MAX, 100)
        
        # Handle edge cases
        if value_min == value_max:
            return 50  # Middle value if no range
        
        # Map sensor value to 0-100 range
        if sensor_value <= value_min:
            dial_value = 0
        elif sensor_value >= value_max:
            dial_value = 100
        else:
            # Linear mapping
            dial_value = int(((sensor_value - value_min) / (value_max - value_min)) * 100)
        
        return max(0, min(100, dial_value))

    def _get_client_for_dial(self, dial_uid: str) -> Optional[VU1APIClient]:
        """Get VU1 API client for a specific dial."""
        # Check if domain data exists yet
        if DOMAIN not in self.hass.data:
            return None
            
        # Find the config entry that contains this dial
        for entry_id, data in self.hass.data[DOMAIN].items():
            if isinstance(data, dict) and "coordinator" in data:
                coordinator = data["coordinator"]
                if coordinator.data and dial_uid in coordinator.data.get("dials", {}):
                    return data["client"]
        return None

    async def async_shutdown(self) -> None:
        """Shutdown the binding manager."""
        # Cancel all debouncers
        for debouncer in self._debouncers.values():
            await debouncer.async_shutdown()
        
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