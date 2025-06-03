"""Configuration entities for VU1 dials."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.components.select import SelectEntity
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


class VU1BoundEntitySelect(VU1ConfigEntityBase, SelectEntity):
    """Select entity for bound sensor."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the bound entity select."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_bound_entity"
        self._attr_name = "Bound sensor"
        self._attr_icon = "mdi:link"
        self._update_options()

    def _update_options(self) -> None:
        """Update available options."""
        entity_registry = er.async_get(self.hass)
        options = ["None"]
        
        for entity in entity_registry.entities.values():
            if entity.domain in ["sensor", "input_number", "number", "counter"]:
                state = self.hass.states.get(entity.entity_id)
                if state and state.state not in ["unknown", "unavailable"]:
                    try:
                        float(state.state)
                        display_name = entity.name or entity.entity_id
                        options.append(f"{entity.entity_id}|{display_name}")
                    except (ValueError, TypeError):
                        pass
        
        self._attr_options = options

    @property
    def current_option(self) -> str:
        """Return current option."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        bound_entity = config.get("bound_entity")
        
        if not bound_entity:
            return "None"
            
        # Find display option
        for option in self._attr_options:
            if "|" in option and option.split("|")[0] == bound_entity:
                return option
        return "None"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option == "None":
            bound_entity = None
        else:
            bound_entity = option.split("|")[0] if "|" in option else option
            
        await self._update_config(bound_entity=bound_entity)


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


class VU1UpdateModeSelect(VU1ConfigEntityBase, SelectEntity):
    """Select entity for update mode."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the update mode select."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_update_mode"
        self._attr_name = "Update mode"
        self._attr_icon = "mdi:update"
        self._attr_options = ["automatic", "manual"]

    @property
    def current_option(self) -> str:
        """Return current option."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        return config.get("update_mode", "manual")

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._update_config(update_mode=option)