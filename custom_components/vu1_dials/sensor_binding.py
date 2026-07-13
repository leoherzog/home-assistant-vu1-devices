"""Sensor binding system for VU1 dials."""
import functools
import logging
import re
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
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
from .coordinator import _get_dial_client_and_coordinator
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
        # Track active bindings: dial_uid -> {entity_id, config, dial_data, last_state}
        self._bindings: dict[str, dict[str, Any]] = {}
        # Track state change listeners with reference counting:
        # entity_id -> {"unsub": unsubscribe_callable, "count": number_of_dials_using_it}
        self._listeners: dict[str, dict[str, Any]] = {}
        self._config_manager = async_get_config_manager(hass)
        # Debounce API calls to prevent rapid updates: dial_uid -> debouncer
        self._debouncers: dict[str, Debouncer] = {}

    async def async_update_bindings(
        self, coordinator_data: dict[str, Any], entry_id: str
    ) -> None:
        """Update bindings based on current dial configurations."""
        # Clean up old bindings for dials that no longer exist. The manager is
        # shared across config entries, so only prune dials owned by the calling
        # entry — otherwise each entry's poll would tear down the others.
        dial_data = coordinator_data.get("dials", {})
        existing_dials = set(dial_data.keys())
        owned_dials = {
            dial_uid for dial_uid, binding in self._bindings.items()
            if binding.get("entry_id") == entry_id
        }

        for dial_uid in owned_dials - existing_dials:
            await self._remove_binding(dial_uid)

        # Update bindings for current dials - checks config and creates/updates as needed
        for dial_uid in existing_dials:
            config = self._config_manager.get_dial_config(dial_uid)
            await self._update_binding(dial_uid, config, dial_data[dial_uid], entry_id)

    async def _update_binding(
        self, dial_uid: str, config: dict[str, Any], dial_data: dict[str, Any], entry_id: str
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
                await self._create_binding(dial_uid, bound_entity, config, dial_data, entry_id)
            else:
                # Same entity - update the stored config and only re-apply the
                # current sensor value when the config actually changed (e.g. a
                # new range/mapping). Re-applying unconditionally would re-issue
                # an identical API call on every coordinator poll.
                old_config = existing_binding.get("config")
                existing_binding["config"] = config.copy()
                existing_binding["dial_data"] = dial_data.copy()
                if old_config != config:
                    current_state = self.hass.states.get(bound_entity)
                    if current_state:
                        await self._apply_sensor_value_from_state(dial_uid, current_state)
        else:
            # No binding exists for this dial - create one
            await self._create_binding(dial_uid, bound_entity, config, dial_data, entry_id)

    async def _create_binding(
        self,
        dial_uid: str,
        entity_id: str,
        config: dict[str, Any],
        dial_data: dict[str, Any],
        entry_id: str,
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

        # Store binding info (no client cached — always look up fresh to avoid stale refs)
        self._bindings[dial_uid] = {
            "entity_id": entity_id,
            "config": config.copy(),
            "dial_data": dial_data.copy(),
            "last_state": None,  # Store the most recent state for debounced processing
            "entry_id": entry_id,  # Owning config entry, so shared-manager pruning is scoped
        }

        # Create debouncer to limit API calls (5 second cooldown per dial)
        self._debouncers[dial_uid] = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=DEBOUNCE_SECONDS,
            immediate=True,
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

        # Apply saved backlight color once during binding creation
        backlight_color = config.get(CONF_BACKLIGHT_COLOR)
        if backlight_color and client:
            try:
                await client.set_dial_backlight(
                    dial_uid, backlight_color[0], backlight_color[1], backlight_color[2]
                )
            except VU1APIError as err:
                _LOGGER.error("Failed to set initial backlight for dial %s: %s", dial_uid, err)

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
        # Always get a fresh client reference to avoid stale refs after config entry reload
        client = self._get_client_for_dial(dial_uid)
        if not client:
            _LOGGER.warning("No client available for dial %s, skipping sensor update", dial_uid)
            return

        try:
            # Parse numeric value from sensor state
            sensor_value = self._parse_sensor_value(state)
            if sensor_value is None:
                return

            # Map sensor range to dial 0-100% range
            dial_value = self._map_value_to_dial(sensor_value, config)
            
            # Update dial position
            await client.set_dial_value(dial_uid, dial_value)

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
        raw = state.state
        # Reject explicit non-numeric / empty states up front.
        if raw in (STATE_UNKNOWN, STATE_UNAVAILABLE, None, ""):
            return None

        try:
            # Direct conversion handles plain numbers and scientific
            # notation (e.g. "1.5e-3").
            return float(raw)
        except (ValueError, TypeError):
            pass

        text = str(raw)

        # Ambiguous grouping/decimal separators (e.g. "1,234 W") can't be
        # parsed reliably — skip rather than silently returning a wrong value.
        if re.search(r"\d[.,]\d{3}(?:\D|$)", text):
            _LOGGER.debug("Ambiguous numeric format %r for sensor; skipping", text)
            return None

        # Extract a leading numeric value, including scientific notation
        # (e.g. "23.5 C" -> 23.5).
        match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
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
        result = _get_dial_client_and_coordinator(self.hass, dial_uid)
        return result[0] if result else None

    async def async_reconfigure_dial_binding(self, dial_uid: str) -> None:
        """Reconfigure binding for a specific dial after configuration changes.

        This is the public method that should be called when a dial's configuration
        has been updated and the binding needs to be refreshed.
        """
        # Get the updated configuration
        config = self._config_manager.get_dial_config(dial_uid)

        # Find the owning entry (and its dial data) from the coordinator
        result = _get_dial_client_and_coordinator(self.hass, dial_uid)
        if result is None:
            _LOGGER.warning("Could not find dial data for %s during reconfiguration", dial_uid)
            return

        _client, coordinator = result
        dial_data = coordinator.data["dials"][dial_uid]

        # Update the binding using our private method
        await self._update_binding(dial_uid, config, dial_data, coordinator.config_entry.entry_id)
        _LOGGER.info("Reconfigured binding for dial %s", dial_uid)

    async def async_remove_binding(self, dial_uid: str) -> None:
        """Public interface for removing a single dial's binding."""
        await self._remove_binding(dial_uid)

    @callback
    def async_get_bindings_summary(self) -> dict[str, dict[str, Any]]:
        """Return a redaction-safe summary of active bindings.

        Public accessor for diagnostics so callers don't have to read the
        private ``_bindings`` mapping.
        """
        return {
            dial_uid: {
                "entity_id": binding.get("entity_id"),
                "has_last_state": binding.get("last_state") is not None,
            }
            for dial_uid, binding in self._bindings.items()
        }


@callback
def async_get_binding_manager(hass: HomeAssistant) -> VU1SensorBindingManager:
    """Get the sensor binding manager."""
    if f"{DOMAIN}_binding_manager" not in hass.data:
        hass.data[f"{DOMAIN}_binding_manager"] = VU1SensorBindingManager(hass)
    return hass.data[f"{DOMAIN}_binding_manager"]
