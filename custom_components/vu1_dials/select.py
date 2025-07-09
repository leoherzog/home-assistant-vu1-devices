"""Support for VU1 dial behavior preset select entities."""
import logging
from typing import Any, Dict

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_config import async_get_config_manager

_LOGGER = logging.getLogger(__name__)

# Preset configurations matching the web UI
BEHAVIOR_PRESETS = {
    "responsive": {
        "name": "Responsive",
        "dial_easing_period": 50,
        "dial_easing_step": 20,
        "backlight_easing_period": 50,
        "backlight_easing_step": 20,
        "description": "Dial is very responsive but may overshoot on large changes"
    },
    "balanced": {
        "name": "Balanced",
        "dial_easing_period": 50,
        "dial_easing_step": 5,
        "backlight_easing_period": 50,
        "backlight_easing_step": 10,
        "description": "Balance between responsive and smooth dial"
    },
    "smooth": {
        "name": "Smooth",
        "dial_easing_period": 50,
        "dial_easing_step": 1,
        "backlight_easing_period": 50,
        "backlight_easing_step": 5,
        "description": "Dial moves slowly with minimum overshoot"
    },
    "custom": {
        "name": "Custom",
        "description": "Manual configuration"
    }
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 select entities."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    
    # Create behavior select entity for each dial
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1BehaviorSelect(coordinator, dial_uid, dial_info))

    async_add_entities(entities)


class VU1BehaviorSelect(CoordinatorEntity, SelectEntity):
    """Select entity for dial behavior presets."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the behavior select entity."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_behavior_preset"
        self._attr_name = "Dial behavior"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:tune"
        self._attr_options = [preset["name"] for preset in BEHAVIOR_PRESETS.values()]

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        return {
            "identifiers": {(DOMAIN, self._dial_uid)},
            "name": dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}"),
            "manufacturer": "Streacom",
            "model": "VU1 Dial",
            "via_device": (DOMAIN, self.coordinator.server_device_identifier),
        }

    @property
    def current_option(self) -> str:
        """Return the currently selected option."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        
        # Check if current values match any preset
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_key == "custom":
                continue
            
            # Convert both config and preset values to int for comparison
            # to handle cases where config values might be stored as strings
            try:
                config_dial_period = int(config.get("dial_easing_period", 50))
                config_dial_step = int(config.get("dial_easing_step", 5))
                config_backlight_period = int(config.get("backlight_easing_period", 50))
                config_backlight_step = int(config.get("backlight_easing_step", 5))
                
                if (config_dial_period == preset_data["dial_easing_period"] and
                    config_dial_step == preset_data["dial_easing_step"] and
                    config_backlight_period == preset_data["backlight_easing_period"] and
                    config_backlight_step == preset_data["backlight_easing_step"]):
                    return preset_data["name"]
            except (ValueError, TypeError):
                # If any conversion fails, skip this preset comparison
                continue
        
        return "Custom"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Find the preset configuration
        preset_config = None
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_data["name"] == option:
                preset_config = preset_data
                break
        
        if not preset_config or option == "Custom":
            # Custom option selected, don't change values
            return
        
        # Update configuration with preset values
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(self._dial_uid)
        config_updates = {
            **current_config,
            "dial_easing_period": preset_config["dial_easing_period"],
            "dial_easing_step": preset_config["dial_easing_step"],
            "backlight_easing_period": preset_config["backlight_easing_period"],
            "backlight_easing_step": preset_config["backlight_easing_step"],
        }
        
        await config_manager.async_update_dial_config(self._dial_uid, config_updates)
        
        # Apply settings to hardware
        await self._apply_easing_config(preset_config)
        
        # Update sensor bindings if needed
        from .sensor_binding import async_get_binding_manager
        binding_manager = async_get_binding_manager(self.hass)
        if binding_manager:
            await binding_manager.async_reconfigure_dial_binding(self._dial_uid)
        
        # Request coordinator refresh to update state
        await self.coordinator.async_request_refresh()
        
        _LOGGER.info("Applied %s behavior preset to dial %s", option, self._dial_uid)

    async def _apply_easing_config(self, preset_config: Dict[str, Any]) -> None:
        """Apply easing configuration to server."""
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if result:
            client, coordinator = result
            try:
                # Apply dial easing
                await client.set_dial_easing(
                    self._dial_uid,
                    preset_config["dial_easing_period"],
                    preset_config["dial_easing_step"]
                )
                # Apply backlight easing
                await client.set_backlight_easing(
                    self._dial_uid,
                    preset_config["backlight_easing_period"],
                    preset_config["backlight_easing_step"]
                )
            except Exception as err:
                _LOGGER.error("Failed to apply behavior preset for %s: %s", self._dial_uid, err)

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
            # Trigger immediate state update to check for preset match
            self.async_schedule_update_ha_state()

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        
        # Find current preset or custom
        current = self.current_option
        description = "Custom configuration"
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_data["name"] == current:
                description = preset_data["description"]
                break
        
        return {
            "description": description,
            "dial_easing_period": config.get("dial_easing_period", 50),
            "dial_easing_step": config.get("dial_easing_step", 5),
            "backlight_easing_period": config.get("backlight_easing_period", 50),
            "backlight_easing_step": config.get("backlight_easing_step", 5),
        }