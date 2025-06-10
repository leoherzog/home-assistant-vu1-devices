"""Configuration entities for VU1 dials."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._dial_uid)},
            "name": self._dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}"),
            "manufacturer": "Streacom",
            "model": "VU1 Dial",
            "via_device": (DOMAIN, f"vu1_server_{self.coordinator.client.host}_{self.coordinator.client.port}"),
        }

    async def _update_config(self, **config_updates) -> None:
        """Update dial configuration."""
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(self._dial_uid)
        new_config = {**current_config, **config_updates}
        await config_manager.async_update_dial_config(self._dial_uid, new_config)
        
        # Update sensor bindings
        binding_manager = async_get_binding_manager(self.hass)
        await binding_manager._update_binding(self._dial_uid, new_config, self._dial_data)


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

    @property
    def native_value(self) -> float:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("value_min", 0)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        await self._update_config(value_min=value)


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

    @property
    def native_value(self) -> float:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("value_max", 100)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        await self._update_config(value_max=value)


class VU1BacklightRedNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for backlight red value."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the backlight red number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_backlight_red"
        self._attr_name = "Backlight red"
        self._attr_icon = "mdi:palette"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_device_class = NumberDeviceClass.ILLUMINANCE

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("backlight_color", [100, 100, 100])[0]

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        color = config.get("backlight_color", [100, 100, 100]).copy()
        color[0] = int(value)
        await self._update_config(backlight_color=color)


class VU1BacklightGreenNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for backlight green value."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the backlight green number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_backlight_green"
        self._attr_name = "Backlight green"
        self._attr_icon = "mdi:palette"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_device_class = NumberDeviceClass.ILLUMINANCE

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("backlight_color", [100, 100, 100])[1]

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        color = config.get("backlight_color", [100, 100, 100]).copy()
        color[1] = int(value)
        await self._update_config(backlight_color=color)


class VU1BacklightBlueNumber(VU1ConfigEntityBase, NumberEntity):
    """Number entity for backlight blue value."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the backlight blue number."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_backlight_blue"
        self._attr_name = "Backlight blue"
        self._attr_icon = "mdi:palette"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_device_class = NumberDeviceClass.ILLUMINANCE

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("backlight_color", [100, 100, 100])[2]

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        color = config.get("backlight_color", [100, 100, 100]).copy()
        color[2] = int(value)
        await self._update_config(backlight_color=color)


class VU1DialNameText(VU1ConfigEntityBase, TextEntity):
    """Text entity for dial name."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the dial name text."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_dial_name"
        self._attr_name = "Dial name"
        self._attr_icon = "mdi:rename"
        self._attr_max = 50

    @property
    def native_value(self) -> str:
        """Return the current value."""
        return self._dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}")

    async def async_set_value(self, value: str) -> None:
        """Update the value."""
        # Update server via API
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if result:
            client, coordinator = result
            try:
                await client.set_dial_name(self._dial_uid, value)
                await coordinator.async_request_refresh()
            except Exception as err:
                _LOGGER.error("Failed to set dial name for %s: %s", self._dial_uid, err)


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

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("dial_easing_period", 50)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        await self._update_config(dial_easing_period=int(value))
        await self._apply_easing_config()

    async def _apply_easing_config(self) -> None:
        """Apply easing configuration to server."""
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if result:
            client, coordinator = result
            config_manager = async_get_config_manager(self.hass)
            config = config_manager.get_dial_config(self._dial_uid)
            try:
                await client.set_dial_easing(
                    self._dial_uid,
                    config.get("dial_easing_period", 50),
                    config.get("dial_easing_step", 5)
                )
            except Exception as err:
                _LOGGER.error("Failed to set dial easing for %s: %s", self._dial_uid, err)


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

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("dial_easing_step", 5)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        await self._update_config(dial_easing_step=int(value))
        await self._apply_easing_config()

    async def _apply_easing_config(self) -> None:
        """Apply easing configuration to server."""
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if result:
            client, coordinator = result
            config_manager = async_get_config_manager(self.hass)
            config = config_manager.get_dial_config(self._dial_uid)
            try:
                await client.set_dial_easing(
                    self._dial_uid,
                    config.get("dial_easing_period", 50),
                    config.get("dial_easing_step", 5)
                )
            except Exception as err:
                _LOGGER.error("Failed to set dial easing for %s: %s", self._dial_uid, err)


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

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("backlight_easing_period", 50)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        await self._update_config(backlight_easing_period=int(value))
        await self._apply_easing_config()

    async def _apply_easing_config(self) -> None:
        """Apply easing configuration to server."""
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if result:
            client, coordinator = result
            config_manager = async_get_config_manager(self.hass)
            config = config_manager.get_dial_config(self._dial_uid)
            try:
                await client.set_backlight_easing(
                    self._dial_uid,
                    config.get("backlight_easing_period", 50),
                    config.get("backlight_easing_step", 5)
                )
            except Exception as err:
                _LOGGER.error("Failed to set backlight easing for %s: %s", self._dial_uid, err)


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

    @property
    def native_value(self) -> int:
        """Return the current value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("backlight_easing_step", 5)

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        await self._update_config(backlight_easing_step=int(value))
        await self._apply_easing_config()

    async def _apply_easing_config(self) -> None:
        """Apply easing configuration to server."""
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if result:
            client, coordinator = result
            config_manager = async_get_config_manager(self.hass)
            config = config_manager.get_dial_config(self._dial_uid)
            try:
                await client.set_backlight_easing(
                    self._dial_uid,
                    config.get("backlight_easing_period", 50),
                    config.get("backlight_easing_step", 5)
                )
            except Exception as err:
                _LOGGER.error("Failed to set backlight easing for %s: %s", self._dial_uid, err)


class VU1UpdateModeSensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing current update mode."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the update mode sensor."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_update_mode_status"
        self._attr_name = "Update mode"
        self._attr_icon = "mdi:update"

    async def async_added_to_hass(self) -> None:
        """Register for configuration change notifications."""
        await super().async_added_to_hass()
        
        # Register as a listener for configuration changes
        config_manager = async_get_config_manager(self.hass)
        config_manager.async_add_listener(self._dial_uid, self._on_config_change)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from configuration change notifications."""
        await super().async_will_remove_from_hass()
        
        # Unregister as a listener
        config_manager = async_get_config_manager(self.hass)
        config_manager.async_remove_listener(self._dial_uid, self._on_config_change)

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
            
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("update_mode", "manual").title()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        if not self.hass:
            return {}
            
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        
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

    async def async_added_to_hass(self) -> None:
        """Register for configuration change notifications."""
        await super().async_added_to_hass()
        
        # Register as a listener for configuration changes
        config_manager = async_get_config_manager(self.hass)
        config_manager.async_add_listener(self._dial_uid, self._on_config_change)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from configuration change notifications."""
        await super().async_will_remove_from_hass()
        
        # Unregister as a listener
        config_manager = async_get_config_manager(self.hass)
        config_manager.async_remove_listener(self._dial_uid, self._on_config_change)

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
            
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        
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
            
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        
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