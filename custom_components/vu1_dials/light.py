"""Support for VU1 dial backlight light entities."""
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_RGB_COLOR,
    ATTR_BRIGHTNESS,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .vu1_api import VU1APIClient
from .device_config import async_get_config_manager

if TYPE_CHECKING:
    from . import VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 backlight light entities."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client: VU1APIClient = data["client"]

    entities = []
    
    # Create backlight light entities for each dial
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1BacklightLight(coordinator, client, dial_uid, dial_info))

    async_add_entities(entities)


class VU1BacklightLight(CoordinatorEntity, LightEntity):
    """Representation of a VU1 dial backlight light entity."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: Dict[str, Any],
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
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        return {
            "identifiers": {(DOMAIN, self._dial_uid)},
            "name": dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}"),
            "manufacturer": "Streacom",
            "model": "VU1 Dial",
            "via_device": (DOMAIN, f"vu1_server_{self._client.host}_{self._client.port}"),
        }

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        # For backlight, we consider it "on" if any RGB component is > 0
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        backlight_color = config.get("backlight_color", [100, 100, 100])
        return any(c > 0 for c in backlight_color)

    @property
    def brightness(self) -> Optional[int]:
        """Return the brightness of this light between 0..255."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        backlight_color = config.get("backlight_color", [100, 100, 100])
        
        # Convert max RGB component (0-100) to brightness (0-255)
        max_component = max(backlight_color) if backlight_color else 0
        return int((max_component / 100) * 255)

    @property
    def rgb_color(self) -> Optional[Tuple[int, int, int]]:
        """Return the RGB color value."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)
        backlight_color = config.get("backlight_color", [100, 100, 100])
        
        # Convert from 0-100 range to 0-255 range
        return tuple(int((c / 100) * 255) for c in backlight_color)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Instruct the light to turn on."""
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(self._dial_uid)
        
        # Start with current color
        new_color = current_config.get("backlight_color", [100, 100, 100]).copy()
        
        # Handle RGB color change
        if ATTR_RGB_COLOR in kwargs:
            rgb = kwargs[ATTR_RGB_COLOR]
            # Convert from 0-255 range to 0-100 range
            new_color = [int((c / 255) * 100) for c in rgb]
        
        # Handle brightness change (scales the current color)
        elif ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            # Convert brightness (0-255) to scale factor (0-1)
            scale = brightness / 255
            # Scale current color, ensuring at least some brightness if brightness > 0
            if brightness > 0:
                # If all components are 0, default to white
                if all(c == 0 for c in new_color):
                    new_color = [100, 100, 100]
                # Scale to desired brightness
                max_current = max(new_color) if new_color else 100
                scale_factor = (scale * 100) / max_current if max_current > 0 else scale
                new_color = [int(c * scale_factor) for c in new_color]
            else:
                # Brightness 0 means turn off
                new_color = [0, 0, 0]
        
        # If no specific color/brightness was provided, turn on with current or default color
        elif all(c == 0 for c in new_color):
            new_color = [100, 100, 100]  # Default to white
        
        # Ensure values are in valid range
        new_color = [max(0, min(100, c)) for c in new_color]
        
        # Update configuration
        await config_manager.async_update_dial_config(
            self._dial_uid, {"backlight_color": new_color}
        )
        
        # Apply to hardware
        try:
            await self._client.set_dial_backlight(
                self._dial_uid, new_color[0], new_color[1], new_color[2]
            )
        except Exception as err:
            _LOGGER.error("Failed to set backlight for %s: %s", self._dial_uid, err)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Instruct the light to turn off."""
        config_manager = async_get_config_manager(self.hass)
        
        # Set color to black (off)
        await config_manager.async_update_dial_config(
            self._dial_uid, {"backlight_color": [0, 0, 0]}
        )
        
        # Apply to hardware
        try:
            await self._client.set_dial_backlight(self._dial_uid, 0, 0, 0)
        except Exception as err:
            _LOGGER.error("Failed to turn off backlight for %s: %s", self._dial_uid, err)
            raise

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
        """No polling needed, we rely on event updates."""
        return False