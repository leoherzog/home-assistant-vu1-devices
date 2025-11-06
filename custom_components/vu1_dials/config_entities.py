"""Configuration entities for VU1 dials."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .device_config import async_get_config_manager
from .sensor_binding import async_get_binding_manager

_LOGGER = logging.getLogger(__name__)

class VU1ConfigEntityBase(CoordinatorEntity):
    """Base class for VU1 configuration entities."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the config entity."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._dial_data = dial_data
        self._config_manager = async_get_config_manager(coordinator.hass)
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_has_entity_name = True

    async def async_added_to_hass(self) -> None:
        """Register for configuration change notifications."""
        await super().async_added_to_hass()
        
        # Register as a listener for configuration changes
        self._config_manager.async_add_listener(self._dial_uid, self._on_config_change)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from configuration change notifications."""
        await super().async_will_remove_from_hass()
        
        # Unregister as a listener
        self._config_manager.async_remove_listener(self._dial_uid, self._on_config_change)

    async def _on_config_change(self, dial_uid: str, config: Dict[str, Any]) -> None:
        """Handle configuration changes from external sources."""
        if dial_uid == self._dial_uid:
            # Update local state from configuration and trigger UI update
            await self._sync_from_config()
            self.async_schedule_update_ha_state()

    async def _sync_from_config(self) -> None:
        """Sync entity state from configuration. Override in subclasses."""
        pass

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._dial_uid)},
            "name": self._dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}"),
            "manufacturer": "Streacom",
            "model": "VU1 Dial",
            "via_device": (DOMAIN, self.coordinator.server_device_identifier),
        }

    async def _update_config(self, **config_updates) -> None:
        """Update dial configuration with optimized sensor binding handling."""
        current_config = self._config_manager.get_dial_config(self._dial_uid)
        new_config = {**current_config, **config_updates}
        
        # Save the configuration first
        await self._config_manager.async_update_dial_config(self._dial_uid, new_config)
        
        # Only update sensor bindings if binding-related keys changed
        binding_keys = {"bound_entity", "update_mode", "value_min", "value_max"}
        if any(key in config_updates for key in binding_keys):
            binding_manager = async_get_binding_manager(self.hass)
            await binding_manager._update_binding(self._dial_uid, new_config, self._dial_data)
        
        # If easing values changed, trigger behavior select update
        easing_keys = {
            "dial_easing_period", "dial_easing_step", 
            "backlight_easing_period", "backlight_easing_step"
        }
        if any(key in config_updates for key in easing_keys):
            await self._trigger_behavior_select_update()
    
    async def _trigger_behavior_select_update(self) -> None:
        """Trigger behavior select entity to update its state."""
        from homeassistant.helpers import entity_registry as er
        
        # Find the behavior select entity
        entity_registry = er.async_get(self.hass)
        behavior_entity_id = entity_registry.async_get_entity_id(
            "select", DOMAIN, f"{self._dial_uid}_behavior_preset"
        )
        
        if behavior_entity_id:
            # Force the entity to update by triggering a state write
            # This will cause the select entity to recalculate its current_option
            if self.hass.states.get(behavior_entity_id):
                # Get current state and write it back to trigger update
                current_state = self.hass.states.get(behavior_entity_id)
                self.hass.states.async_set(
                    behavior_entity_id, 
                    current_state.state,
                    current_state.attributes,
                    force_update=True
                )
                _LOGGER.debug("Triggered behavior select update for %s", self._dial_uid)

    async def _apply_easing_config_to_server(
        self, 
        easing_type: str, 
        new_period: Optional[int] = None, 
        new_step: Optional[int] = None
    ) -> None:
        """Apply easing configuration to server with specific new values.
        
        Args:
            easing_type: Either "dial" or "backlight" to specify which easing to configure
            new_period: New period value, or None to use current config
            new_step: New step value, or None to use current config
        """
        from . import _get_dial_client_and_coordinator
        
        _LOGGER.debug("Attempting to apply %s easing config for %s", easing_type, self._dial_uid)
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        
        if not result:
            _LOGGER.error("Failed to get client/coordinator for dial %s - cannot apply easing config", self._dial_uid)
            raise HomeAssistantError(f"Cannot communicate with dial {self._dial_uid}")
            
        client, coordinator = result

        # Mark grace period immediately after getting valid client/coordinator
        # This prevents sync loops even if subsequent operations fail
        coordinator.mark_behavior_change_from_ha(self._dial_uid)

        config = self._config_manager.get_dial_config(self._dial_uid)
        
        # Determine which config keys and API method to use
        if easing_type == "dial":
            period_key = "dial_easing_period"
            step_key = "dial_easing_step"
            default_period = 50
            default_step = 5
            api_method = client.set_dial_easing
        elif easing_type == "backlight":
            period_key = "backlight_easing_period"
            step_key = "backlight_easing_step"
            default_period = 50
            default_step = 5
            api_method = client.set_backlight_easing
        else:
            raise ValueError(f"Invalid easing_type: {easing_type}")
        
        # Use new values if provided, otherwise use current config
        period = new_period if new_period is not None else config.get(period_key, default_period)
        step = new_step if new_step is not None else config.get(step_key, default_step)
        
        try:
            _LOGGER.info("Setting %s easing for %s: period=%d, step=%d", easing_type, self._dial_uid, period, step)
            await api_method(self._dial_uid, period, step)
            await coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set %s easing for %s: %s", easing_type, self._dial_uid, err)
            raise HomeAssistantError(f"Failed to apply {easing_type} easing: {err}")

class VU1ValueMinNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for minimum value."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the value min number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_value_min"
        self._attr_name = "Value range minimum"
        self._attr_icon = "mdi:numeric"
        self._attr_native_min_value = -1000
        self._attr_native_max_value = 1000
        self._attr_native_step = 0.1
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get("value_min", 0)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get("value_min", 0)

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        # Store old value for rollback
        old_value = self._attr_native_value
        
        # Immediately update local state for responsive UI
        self._attr_native_value = value
        self.async_write_ha_state()
        
        try:
            # Save to configuration
            await self._update_config(value_min=value)
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(f"Failed to update value range minimum: {err}")

class VU1ValueMaxNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for maximum value."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the value max number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_value_max"
        self._attr_name = "Value range maximum"
        self._attr_icon = "mdi:numeric"
        self._attr_native_min_value = -1000
        self._attr_native_max_value = 1000
        self._attr_native_step = 0.1
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get("value_max", 100)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get("value_max", 100)

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        # Store old value for rollback
        old_value = self._attr_native_value
        
        # Immediately update local state for responsive UI
        self._attr_native_value = value
        self.async_write_ha_state()
        
        try:
            # Save to configuration
            await self._update_config(value_max=value)
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(f"Failed to update value range maximum: {err}")

class VU1DialEasingPeriodNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for dial easing period."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the dial easing period number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_dial_easing_period"
        self._attr_name = "Dial easing period"
        self._attr_icon = "mdi:timer"
        self._attr_native_min_value = 10
        self._attr_native_max_value = 1000
        self._attr_native_step = 10
        self._attr_native_unit_of_measurement = "ms"
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get("dial_easing_period", 50)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get("dial_easing_period", 50)

    @property
    def native_value(self) -> int:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        # Store old value for rollback
        old_value = self._attr_native_value
        
        # Immediately update local state for responsive UI
        self._attr_native_value = int(value)
        self.async_write_ha_state()
        
        try:
            # Apply to hardware first
            await self._apply_easing_config_to_server("dial", new_period=int(value))
            # If successful, save config
            await self._update_config(dial_easing_period=int(value))
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(f"Failed to update dial easing period: {err}")


class VU1DialEasingStepNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for dial easing step."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the dial easing step number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_dial_easing_step"
        self._attr_name = "Dial easing step"
        self._attr_icon = "mdi:stairs"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "%"
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get("dial_easing_step", 5)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get("dial_easing_step", 5)

    @property
    def native_value(self) -> int:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        # Store old value for rollback
        old_value = self._attr_native_value
        
        # Immediately update local state for responsive UI
        self._attr_native_value = int(value)
        self.async_write_ha_state()
        
        try:
            # Apply to hardware first
            await self._apply_easing_config_to_server("dial", new_step=int(value))
            # If successful, save config
            await self._update_config(dial_easing_step=int(value))
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(f"Failed to update dial easing step: {err}")


class VU1BacklightEasingPeriodNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for backlight easing period."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the backlight easing period number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_backlight_easing_period"
        self._attr_name = "Backlight easing period"
        self._attr_icon = "mdi:timer"
        self._attr_native_min_value = 10
        self._attr_native_max_value = 1000
        self._attr_native_step = 10
        self._attr_native_unit_of_measurement = "ms"
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get("backlight_easing_period", 50)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get("backlight_easing_period", 50)

    @property
    def native_value(self) -> int:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        # Store old value for rollback
        old_value = self._attr_native_value
        
        # Immediately update local state for responsive UI
        self._attr_native_value = int(value)
        self.async_write_ha_state()
        
        try:
            # Apply to hardware first
            await self._apply_easing_config_to_server("backlight", new_period=int(value))
            # If successful, save config
            await self._update_config(backlight_easing_period=int(value))
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(f"Failed to update backlight easing period: {err}")

class VU1BacklightEasingStepNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for backlight easing step."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the backlight easing step number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_backlight_easing_step"
        self._attr_name = "Backlight easing step"
        self._attr_icon = "mdi:stairs"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "%"
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get("backlight_easing_step", 5)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get("backlight_easing_step", 5)

    @property
    def native_value(self) -> int:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        # Store old value for rollback
        old_value = self._attr_native_value
        
        # Immediately update local state for responsive UI
        self._attr_native_value = int(value)
        self.async_write_ha_state()
        
        try:
            # Apply to hardware first
            await self._apply_easing_config_to_server("backlight", new_step=int(value))
            # If successful, save config
            await self._update_config(backlight_easing_step=int(value))
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(f"Failed to update backlight easing step: {err}")

class VU1UpdateModeSensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing current update mode."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the update mode sensor."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_update_mode_status"
        self._attr_name = "Update mode"
        self._attr_icon = "mdi:update"
        # Remove entity_category for sensor entities
        self._attr_entity_category = None

    # Config change listeners inherited from base class

    async def _on_config_change(self, dial_uid: str, config: Dict[str, Any]) -> None:
        """Handle configuration changes."""
        if dial_uid == self._dial_uid:
            # Trigger immediate state update
            self.async_schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """No polling needed, we rely on coordinator updates."""
        return False

    @property
    def native_value(self) -> str:
        """Return the current update mode."""
        if not self.hass:
            return "unknown"
            
        config = self._config_manager.get_dial_config(self._dial_uid)
        return config.get("update_mode", "manual").title()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        if not self.hass:
            return {}
            
        config = self._config_manager.get_dial_config(self._dial_uid)
        
        attrs = {
            "update_mode": config.get("update_mode", "manual"),
        }
        
        if config.get("update_mode") == "automatic":
            attrs.update({
                "bound_entity": config.get("bound_entity"),
                "value_min": config.get("value_min", 0),
                "value_max": config.get("value_max", 100),
            })
        
        return attrs

class VU1BoundEntitySensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing currently bound entity."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the bound entity sensor."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_bound_entity_status"
        self._attr_name = "Bound entity"
        self._attr_icon = "mdi:link"
        # Remove entity_category for sensor entities
        self._attr_entity_category = None

    # Config change listeners inherited from base class

    async def _on_config_change(self, dial_uid: str, config: Dict[str, Any]) -> None:
        """Handle configuration changes."""
        if dial_uid == self._dial_uid:
            # Trigger immediate state update
            self.async_schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """No polling needed, we rely on coordinator updates."""
        return False

    @property
    def native_value(self) -> str:
        """Return the currently bound entity."""
        if not self.hass:
            return "None"
            
        config = self._config_manager.get_dial_config(self._dial_uid)
        
        if config.get("update_mode") != "automatic":
            return "Manual Update Mode"
            
        bound_entity = config.get("bound_entity")
        if not bound_entity:
            return "None"
            
        # Get friendly name if available
        state = self.hass.states.get(bound_entity)
        if state:
            friendly_name = state.attributes.get("friendly_name")
            if friendly_name:
                return friendly_name
                
        return bound_entity

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        if not self.hass:
            return {}
            
        config = self._config_manager.get_dial_config(self._dial_uid)
        
        attrs = {
            "update_mode": config.get("update_mode", "manual"),
            "bound_entity_id": config.get("bound_entity"),
        }
        
        # Add current sensor value if bound
        bound_entity = config.get("bound_entity")
        if bound_entity and config.get("update_mode") == "automatic":
            state = self.hass.states.get(bound_entity)
            if state:
                attrs.update({
                    "sensor_state": state.state,
                    "sensor_unit": state.attributes.get("unit_of_measurement"),
                    "last_updated": state.last_updated.isoformat(),
                })
        
        return attrs
