"""Configuration entities for VU1 dials."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.components.select import SelectEntity
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


class VU1BoundEntitySelect(VU1ConfigEntityBase, SelectEntity):
    """Select entity for bound sensor."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the bound entity select."""
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_bound_entity"
        self._attr_name = "Bound sensor"
        self._attr_icon = "mdi:link"
        self._attr_options = ["None"]  # Default options, will be updated when added to hass

    def _update_options(self) -> None:
        """Update available options."""
        if not self.hass:
            return
            
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
        # Update options when accessed
        self._update_options()
        
        if not self.hass:
            return "None"
            
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
        
    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        # Update options when entity is added to hass
        self._update_options()


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