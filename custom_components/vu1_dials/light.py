"""Support for VU1 dial backlight light entities."""
import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_RGB_COLOR,
    ATTR_BRIGHTNESS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, get_dial_device_info
from .device_config import async_get_config_manager
from .vu1_api import VU1APIClient

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 backlight light entities."""
    coordinator = config_entry.runtime_data.coordinator
    client = config_entry.runtime_data.client

    entities = []
    
    dial_data = coordinator.data.get("dials", {}) if coordinator.data else {}
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1BacklightLight(coordinator, client, dial_uid, dial_info))

    async_add_entities(entities)

    # Register callback for creating entities when new dials are discovered
    async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
        """Create light entities for newly discovered dials."""
        new_entities = []
        for dial_uid, dial_info in new_dials.items():
            _LOGGER.info("Creating light entity for new dial %s", dial_uid)
            new_entities.append(VU1BacklightLight(coordinator, client, dial_uid, dial_info))
        if new_entities:
            async_add_entities(new_entities)

    unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
    config_entry.async_on_unload(unsub)


class VU1BacklightLight(CoordinatorEntity, LightEntity):
    """Representation of a VU1 dial backlight light entity."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: dict[str, Any],
    ) -> None:
        """Initialize the backlight light entity."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_backlight"
        self._attr_name = "Backlight"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:palette"
        
        # Configure color modes
        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {}) if self.coordinator.data else {}
        return get_dial_device_info(self._dial_uid, dial_data, self.coordinator.server_device_identifier)

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        # For backlight, we consider it "on" if any RGB component is > 0
        backlight = self._get_backlight_from_coordinator()
        if not backlight:
            return False
        return any(backlight.get(color, 0) > 0 for color in ["red", "green", "blue"])

    @property
    def brightness(self) -> int | None:
        """Return the brightness of this light between 0..255."""
        backlight = self._get_backlight_from_coordinator()
        if not backlight:
            return 0
        
        # Convert max RGB component (0-100) to brightness (0-255)
        max_component = max(backlight.get(color, 0) for color in ["red", "green", "blue"])
        return round((max_component / 100) * 255)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color value."""
        backlight = self._get_backlight_from_coordinator()
        if not backlight:
            return (255, 255, 255)  # Default to white if no data
        
        # Convert from 0-100 range to 0-255 range
        return tuple(
            round((backlight.get(color, 0) / 100) * 255) 
            for color in ["red", "green", "blue"]
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Instruct the light to turn on."""
        # Start with current hardware color
        backlight = self._get_backlight_from_coordinator()
        if backlight:
            current_rgb = [backlight.get(color, 0) for color in ["red", "green", "blue"]]
        else:
            current_rgb = [100, 100, 100]  # Default to white
        
        new_color = current_rgb.copy()
        
        # Handle RGB color change
        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            # Convert from 0-255 range to 0-100 range
            new_color = [round((c / 255) * 100) for c in rgb]
        
        # Handle brightness change (scales the current color)
        elif ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            if brightness > 0:
                # If all components are 0, default to white
                if all(c == 0 for c in new_color):
                    new_color = [100, 100, 100]
                # Scale color to achieve desired brightness (0-255 -> 0-100)
                scale = brightness / 255
                new_color = [round(c * scale) for c in new_color]
            else:
                # Brightness 0 means turn off
                new_color = [0, 0, 0]
        
        # If no specific color/brightness was provided, turn on with current or default color
        elif all(c == 0 for c in new_color):
            new_color = [100, 100, 100]  # Default to white
        
        # Ensure values are in valid range
        new_color = [max(0, min(100, c)) for c in new_color]
        
        # Apply to hardware first
        try:
            await self._client.set_dial_backlight(
                self._dial_uid, new_color[0], new_color[1], new_color[2]
            )
            
            # Update device config to preserve user's backlight preference for sensor binding
            config_manager = async_get_config_manager(self.hass)
            await config_manager.async_update_dial_config(
                self._dial_uid, {"backlight_color": new_color}
            )
            
            # Small delay to let hardware settle, then refresh coordinator
            await asyncio.sleep(0.1)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set backlight for %s: %s", self._dial_uid, err)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Instruct the light to turn off."""
        # Apply to hardware first
        try:
            await self._client.set_dial_backlight(self._dial_uid, 0, 0, 0)
            
            # Update device config to preserve user's backlight preference for sensor binding
            config_manager = async_get_config_manager(self.hass)
            await config_manager.async_update_dial_config(
                self._dial_uid, {"backlight_color": [0, 0, 0]}
            )
            
            # Small delay to let hardware settle, then refresh coordinator
            await asyncio.sleep(0.1)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off backlight for %s: %s", self._dial_uid, err)
            raise

    def _get_backlight_from_coordinator(self) -> dict[str, int] | None:
        """Get current backlight state from coordinator data."""
        if not self.coordinator.data:
            return None

        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        detailed_status = dial_data.get("detailed_status", {})
        return detailed_status.get("backlight")
