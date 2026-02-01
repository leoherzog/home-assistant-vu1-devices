"""Support for VU1 dial number entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, get_dial_device_info
from .vu1_api import VU1APIClient
from .config_entities import (
    VU1ValueMinNumber,
    VU1ValueMaxNumber,
    VU1DialEasingPeriodNumber,
    VU1DialEasingStepNumber,
    VU1BacklightEasingPeriodNumber,
    VU1BacklightEasingStepNumber,
)

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 number entities."""
    coordinator = config_entry.runtime_data.coordinator
    client = config_entry.runtime_data.client

    entities = []
    
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1DialNumber(coordinator, client, dial_uid, dial_info))
        
        entities.extend([
            VU1ValueMinNumber(coordinator, dial_uid, dial_info),
            VU1ValueMaxNumber(coordinator, dial_uid, dial_info),
            VU1DialEasingPeriodNumber(coordinator, dial_uid, dial_info),
            VU1DialEasingStepNumber(coordinator, dial_uid, dial_info),
            VU1BacklightEasingPeriodNumber(coordinator, dial_uid, dial_info),
            VU1BacklightEasingStepNumber(coordinator, dial_uid, dial_info),
        ])

    async_add_entities(entities)

    # Register callback for creating entities when new dials are discovered
    async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
        """Create number entities for newly discovered dials."""
        new_entities = []
        for dial_uid, dial_info in new_dials.items():
            _LOGGER.info("Creating number entities for new dial %s", dial_uid)
            new_entities.append(VU1DialNumber(coordinator, client, dial_uid, dial_info))
            new_entities.extend([
                VU1ValueMinNumber(coordinator, dial_uid, dial_info),
                VU1ValueMaxNumber(coordinator, dial_uid, dial_info),
                VU1DialEasingPeriodNumber(coordinator, dial_uid, dial_info),
                VU1DialEasingStepNumber(coordinator, dial_uid, dial_info),
                VU1BacklightEasingPeriodNumber(coordinator, dial_uid, dial_info),
                VU1BacklightEasingStepNumber(coordinator, dial_uid, dial_info),
            ])
        if new_entities:
            async_add_entities(new_entities)

    unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
    config_entry.async_on_unload(unsub)


class VU1DialNumber(CoordinatorEntity, NumberEntity):
    """Representation of a VU1 dial number entity."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: dict[str, Any],
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{DOMAIN}_dial_{dial_uid}"
        self._attr_name = "Value"
        self._attr_has_entity_name = True
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = NumberMode.SLIDER
        self._attr_icon = "mdi:gauge"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_data = {}
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        return get_dial_device_info(
            self._dial_uid, dial_data, self.coordinator.server_device_identifier
        )

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if not self.coordinator.data:
            return None
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        detailed_status = dial_data.get("detailed_status", {})
        value = detailed_status.get("value")
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the dial value."""
        try:
            # First, switch to manual mode if currently in automatic mode
            await self._switch_to_manual_mode_if_needed()
            
            # Set the dial value
            await self._client.set_dial_value(self._dial_uid, int(value))
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set dial value for %s: %s", self._dial_uid, err)
            raise

    async def _switch_to_manual_mode_if_needed(self) -> None:
        """Switch to manual mode if currently in automatic mode."""
        from .device_config import async_get_config_manager
        from .sensor_binding import async_get_binding_manager
        
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        
        # Only switch if currently in automatic mode
        if config.get("update_mode") == "automatic":
            _LOGGER.info("Switching dial %s from automatic to manual mode due to manual value change", self._dial_uid)
            
            # Update configuration to manual mode
            await config_manager.async_update_dial_config(
                self._dial_uid, 
                {"update_mode": "manual"}
            )
            
            # Update sensor bindings to remove the automatic binding
            binding_manager = async_get_binding_manager(self.hass)
            await binding_manager.async_reconfigure_dial_binding(self._dial_uid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {"dial_uid": self._dial_uid}
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        
        attributes = {
            "dial_uid": self._dial_uid,
            "dial_name": dial_data.get("dial_name"),
        }

        # Add backlight information from detailed status
        detailed_status = dial_data.get("detailed_status", {})
        backlight = detailed_status.get("backlight", {})
        if backlight:
            attributes.update({
                "backlight_red": backlight.get("red"),
                "backlight_green": backlight.get("green"),
                "backlight_blue": backlight.get("blue"),
            })

        return attributes
