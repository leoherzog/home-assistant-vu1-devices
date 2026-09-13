"""Sensor binding system for VU1 dials."""
from __future__ import annotations

import functools
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_BOUND_ENTITY,
    CONF_UPDATE_MODE,
    CONF_VALUE_MAX,
    CONF_VALUE_MIN,
    DATA_BINDING_MANAGER,
    UPDATE_MODE_AUTOMATIC,
    UPDATE_MODE_MANUAL,
)
from .coordinator import _get_dial_client_and_coordinator
from .device_config import async_get_config_manager
from .vu1_api import VU1APIError

_LOGGER = logging.getLogger(__name__)

__all__ = ["BINDING_KEYS", "VU1SensorBindingManager", "async_get_binding_manager"]

# Debounce settings
DEBOUNCE_SECONDS = 5  # Minimum seconds between API calls per dial

# Config keys that affect how a sensor maps onto a dial
BINDING_KEYS = (CONF_BOUND_ENTITY, CONF_UPDATE_MODE, CONF_VALUE_MIN, CONF_VALUE_MAX)

# A number optionally followed by a unit, e.g. "23.5 °C"; rejects timestamps and dates
_NUMBER_RE = re.compile(r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\D*")


class VU1SensorBindingManager:
    """Manage sensor bindings for VU1 dials."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the binding manager."""
        self.hass = hass
        # Track active bindings: dial_uid -> {entity_id, config, last_state}
        self._bindings: dict[str, dict[str, Any]] = {}
        # Track state change listeners with reference counting:
        # entity_id -> {"unsub": unsubscribe_callable, "count": number_of_dials_using_it}
        self._listeners: dict[str, dict[str, Any]] = {}
        self._config_manager = async_get_config_manager(hass)
        # Debounce API calls to prevent rapid updates: dial_uid -> debouncer
        self._debouncers: dict[str, Debouncer] = {}
        self._missing_entities: set[str] = set()

    async def async_update_bindings(self, coordinator_data: dict[str, Any]) -> None:
        """Update bindings based on current dial configurations."""
        existing_dials = set(coordinator_data["dials"])
        for dial_uid in set(self._bindings) - existing_dials:
            await self._remove_binding(dial_uid)

        for dial_uid in existing_dials:
            await self._update_binding(dial_uid, self._config_manager.get_dial_config(dial_uid))

    async def _update_binding(self, dial_uid: str, config: dict[str, Any]) -> None:
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
                await self._create_binding(dial_uid, bound_entity, config)
            else:
                # Same entity - re-apply only when the mapping changed.
                old_config = existing_binding["config"]
                existing_binding["config"] = config.copy()
                if any(old_config.get(key) != config.get(key) for key in BINDING_KEYS):
                    current_state = self.hass.states.get(bound_entity)
                    if current_state:
                        await self._apply_sensor_value_from_state(dial_uid, current_state)
        else:
            # No binding exists for this dial - create one
            await self._create_binding(dial_uid, bound_entity, config)

    async def _create_binding(self, dial_uid: str, entity_id: str, config: dict[str, Any]) -> None:
        """Create a new sensor binding."""
        # Validate entity exists
        entity_registry = er.async_get(self.hass)
        if not entity_registry.async_get(entity_id) and not self.hass.states.get(entity_id):
            if entity_id not in self._missing_entities:
                self._missing_entities.add(entity_id)
                _LOGGER.warning("Bound entity %s does not exist for dial %s", entity_id, dial_uid)
            return
        self._missing_entities.discard(entity_id)

        # Get client for this dial
        if not _get_dial_client_and_coordinator(self.hass, dial_uid):
            _LOGGER.debug("No client found for dial %s (integration may still be loading)", dial_uid)
            return

        # Store binding info (no client cached — always look up fresh to avoid stale refs)
        self._bindings[dial_uid] = {
            "entity_id": entity_id,
            "config": config.copy(),
            "last_state": None,  # Store the most recent state for debounced processing
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
            debouncer.async_shutdown()

        # Remove binding
        del self._bindings[dial_uid]
        _LOGGER.info("Removed sensor binding for dial %s", dial_uid)

    @callback
    def _async_sensor_state_changed(self, event) -> None:
        """Handle sensor state change."""
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        old_state = event.data["old_state"]

        if not new_state or (old_state and old_state.state == new_state.state):
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
        if not (result := _get_dial_client_and_coordinator(self.hass, dial_uid)):
            _LOGGER.warning("No client available for dial %s, skipping sensor update", dial_uid)
            return
        client, coordinator = result

        try:
            # Parse numeric value from sensor state
            sensor_value = self._parse_sensor_value(state)
            if sensor_value is None:
                return

            # Map sensor range to dial 0-100% range
            dial_value = self._map_value_to_dial(sensor_value, config)
            status = coordinator.data["dials"][dial_uid]["detailed_status"]
            if status.get("value") == dial_value:
                return

            await client.set_dial_value(dial_uid, dial_value)
            status["value"] = dial_value
            coordinator.async_update_listeners()

            _LOGGER.debug(
                "Applied sensor value %s -> dial %s (value: %s)",
                sensor_value, dial_uid, dial_value
            )

        except VU1APIError as err:
            _LOGGER.error("Failed to update dial %s from sensor: %s", dial_uid, err)
        except Exception:
            _LOGGER.exception("Unexpected error updating dial %s from sensor", dial_uid)

    def _parse_sensor_value(self, state: State) -> float | None:
        """Parse sensor state to numeric value."""
        match = _NUMBER_RE.fullmatch(state.state)
        return float(match.group(1)) if match else None

    def _map_value_to_dial(self, sensor_value: float, config: dict[str, Any]) -> int:
        """Map sensor value to dial range (0-100)."""
        value_min = config[CONF_VALUE_MIN]
        value_max = config[CONF_VALUE_MAX]
        if value_min == value_max:
            return 50
        if sensor_value <= value_min:
            return 0
        if sensor_value >= value_max:
            return 100
        return round((sensor_value - value_min) / (value_max - value_min) * 100)

    async def async_reconfigure_dial_binding(self, dial_uid: str) -> None:
        """Reconfigure binding for a specific dial after configuration changes.

        This is the public method that should be called when a dial's configuration
        has been updated and the binding needs to be refreshed.
        """
        await self._update_binding(dial_uid, self._config_manager.get_dial_config(dial_uid))

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
    """Return the sensor binding manager created in async_setup."""
    return hass.data[DATA_BINDING_MANAGER]


async def async_switch_to_manual(hass: HomeAssistant, dial_uid: str) -> None:
    """Switch a dial in automatic mode to manual, dropping its binding."""
    config_manager = async_get_config_manager(hass)
    if config_manager.get_dial_config(dial_uid)[CONF_UPDATE_MODE] == UPDATE_MODE_AUTOMATIC:
        await config_manager.async_update_dial_config(dial_uid, {CONF_UPDATE_MODE: UPDATE_MODE_MANUAL})
        await async_get_binding_manager(hass).async_reconfigure_dial_binding(dial_uid)
