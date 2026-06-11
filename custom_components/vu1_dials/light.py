"""Support for VU1 dial backlight light entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    LightEntity,
    ColorMode,
    ATTR_RGB_COLOR,
    ATTR_BRIGHTNESS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import VU1DialEntity, async_setup_dial_entities
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

    def entity_factory(dial_uid: str, dial_info: dict[str, Any]) -> list:
        return [VU1BacklightLight(coordinator, client, dial_uid, dial_info)]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1BacklightLight(VU1DialEntity, CoordinatorEntity, LightEntity):
    """Representation of a VU1 dial backlight light entity."""

    def __init__(
        self,
        coordinator: "VU1DataUpdateCoordinator",
        client: VU1APIClient,
        dial_uid: str,
        dial_data: dict[str, Any],
    ) -> None:
        """Initialize the backlight light entity."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_backlight"
        self._attr_translation_key = "backlight"

        # Configure color modes
        self._attr_supported_color_modes = {ColorMode.RGB}
        self._attr_color_mode = ColorMode.RGB

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
            return None

        # Convert max RGB component (0-100) to brightness (0-255)
        max_component = max(backlight.get(color, 0) for color in ["red", "green", "blue"])
        return round((max_component / 100) * 255)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color value."""
        backlight = self._get_backlight_from_coordinator()
        if not backlight:
            return None

        # Convert from 0-100 range to 0-255 range
        return tuple(
            round((backlight.get(color, 0) / 100) * 255)
            for color in ["red", "green", "blue"]
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Instruct the light to turn on."""
        # Current hardware color in the device's 0-100 range.
        backlight = self._get_backlight_from_coordinator()
        if backlight:
            current_rgb_100 = [backlight.get(color, 0) for color in ["red", "green", "blue"]]
        else:
            current_rgb_100 = [0, 0, 0]

        # Derive a full-brightness base color in 0-255 space. When the caller
        # supplies an explicit color, use it; otherwise normalize the current
        # device color by its largest component so brightness can scale in both
        # directions without compounding the dimming already baked into the
        # stored 0-100 values.
        if ATTR_RGB_COLOR in kwargs:
            base_rgb = list(kwargs[ATTR_RGB_COLOR])
        else:
            current_max = max(current_rgb_100)
            if current_max > 0:
                base_rgb = [round((c / current_max) * 255) for c in current_rgb_100]
            else:
                base_rgb = [255, 255, 255]  # No color info -> default to white

        # Determine target brightness (0-255). Apply it together with the color
        # (not via elif) so scenes sending both rgb_color and brightness work.
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
        else:
            current_brightness = self.brightness
            brightness = current_brightness if current_brightness else 255

        if brightness <= 0:
            new_color = [0, 0, 0]
        else:
            scale = brightness / 255
            # Scale the 0-255 base color and convert to the device 0-100 range.
            new_color = [round(c * scale * 100 / 255) for c in base_rgb]
            # Clamp dim-but-on results: a nonzero brightness on a nonblack base
            # must never round all components to 0 (which would read as off).
            if any(c > 0 for c in base_rgb) and all(c == 0 for c in new_color):
                max_base = max(base_rgb)
                new_color = [1 if c == max_base else 0 for c in base_rgb]

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

            # Optimistically update coordinator data to avoid UI flicker.
            # The VU1 server queues commands and applies them asynchronously,
            # so polling immediately would return stale state.
            self._update_coordinator_backlight(new_color)
            self.async_write_ha_state()
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

            # Optimistically update coordinator data to avoid UI flicker.
            self._update_coordinator_backlight([0, 0, 0])
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to turn off backlight for %s: %s", self._dial_uid, err)
            raise

    def _update_coordinator_backlight(self, color: list[int]) -> None:
        """Optimistically update coordinator data with new backlight values."""
        if not self.coordinator.data:
            return
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid)
        if dial_data is None:
            return
        if "detailed_status" not in dial_data:
            dial_data["detailed_status"] = {}
        dial_data["detailed_status"]["backlight"] = {
            "red": color[0],
            "green": color[1],
            "blue": color[2],
        }

    def _get_backlight_from_coordinator(self) -> dict[str, int] | None:
        """Get current backlight state from coordinator data."""
        if not self.coordinator.data:
            return None

        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        detailed_status = dial_data.get("detailed_status", {})
        return detailed_status.get("backlight")
