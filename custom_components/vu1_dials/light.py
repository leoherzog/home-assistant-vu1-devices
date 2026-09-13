"""Support for VU1 dial backlight light entities."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import VU1DialEntity, async_setup_dial_entities

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

__all__ = ["async_setup_entry"]

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VU1 backlight light entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str) -> list:
        return [VU1BacklightLight(coordinator, dial_uid)]

    async_setup_dial_entities(config_entry, async_add_entities, entity_factory)


class VU1BacklightLight(VU1DialEntity, LightEntity):
    """Representation of a VU1 dial backlight light entity."""

    def __init__(
        self,
        coordinator: VU1DataUpdateCoordinator,
        dial_uid: str,
    ) -> None:
        """Initialize the backlight light entity."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_backlight"
        self._attr_translation_key = "backlight"

        # The dial hardware is RGBW; the server accepts white but does not report it.
        self._attr_supported_color_modes = {ColorMode.RGBW}
        self._attr_color_mode = ColorMode.RGBW

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return any(self._backlight())

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        return round(max(self._backlight()) / 100 * 255)

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return the RGBW color scaled so its largest channel is 255."""
        backlight = self._backlight()
        if not (peak := max(backlight)):
            return None
        return tuple(round(c / peak * 255) for c in backlight)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Instruct the light to turn on."""
        # The color is full-scale and brightness dims it, so the two never compound.
        base = kwargs.get(ATTR_RGBW_COLOR) or self.rgbw_color or (255, 255, 255, 0)
        brightness = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)

        scale = brightness / 255
        # Scale the 0-255 base color and convert to the device 0-100 range.
        new_color = [round(c * scale * 100 / 255) for c in base]
        # Clamp dim-but-on results: a nonzero brightness on a nonblack base
        # must never round all components to 0 (which would read as off).
        if any(c > 0 for c in base) and all(c == 0 for c in new_color):
            max_base = max(base)
            new_color = [1 if c == max_base else 0 for c in base]

        await self.coordinator.async_set_backlight(self._dial_uid, *new_color)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Instruct the light to turn off."""
        await self.coordinator.async_set_backlight(self._dial_uid, 0, 0, 0, 0)
