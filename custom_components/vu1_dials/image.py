"""Support for VU1 dial image entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .entity import VU1DialEntity, async_setup_dial_entities

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VU1 image entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str) -> list:
        return [VU1DialBackgroundImage(hass, coordinator, dial_uid)]

    async_setup_dial_entities(config_entry, async_add_entities, entity_factory)


class VU1DialBackgroundImage(VU1DialEntity, ImageEntity):
    """Image entity showing the current background image of a VU1 dial."""

    def __init__(self, hass: HomeAssistant, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the dial background image entity."""
        VU1DialEntity.__init__(self, coordinator, dial_uid)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{dial_uid}_background_image"
        self._attr_translation_key = "background_image"
        self._attr_content_type = "image/png"
        self._attr_image_last_updated = dt_util.utcnow()
        self._image_bytes: bytes | None = None
        self._image_file = self._get_current_image_file()
        self._image_crc = self._get_current_image_crc()

    async def async_image(self) -> bytes | None:
        """Return the current dial background image."""
        if not self._get_current_image_file():
            return None
        if self._image_bytes is None:
            try:
                self._image_bytes = await self.coordinator.client.get_dial_image(self._dial_uid) or None
            except HomeAssistantError as err:
                _LOGGER.error("Failed to fetch image for dial %s: %s", self._dial_uid, err)
                return None
            if self._image_bytes:
                self._attr_content_type = self._sniff_content_type(self._image_bytes)
        return self._image_bytes

    def _get_current_image_file(self) -> str | None:
        """Get the current image file path from coordinator data."""
        return self.dial_data.get("image_file") or self.dial_data.get("detailed_status", {}).get("image_file")

    def _get_current_image_crc(self) -> str | None:
        """Get the current image CRC (8-digit hex) from coordinator data."""
        return self.dial_data.get("image_crc")

    @staticmethod
    def _sniff_content_type(image_data: bytes) -> str:
        """Determine the image content type from its magic bytes."""
        if image_data.startswith(b"\xff\xd8"):
            return "image/jpeg"
        # PNG signature, and the safe default for VU1 dial faces.
        return "image/png"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        attributes = super().extra_state_attributes or {}

        # Add image file information
        image_file = self._get_current_image_file()
        if image_file:
            # Extract just the filename for display
            filename = image_file.replace("\\", "/").split("/")[-1]
            attributes["image_filename"] = filename
            attributes["image_file_path"] = image_file

        # Add image change status
        detailed_status = self.dial_data.get("detailed_status", {})
        if "image_changed" in detailed_status:
            attributes["image_changed"] = detailed_status["image_changed"]

        # Add technical specifications
        attributes["display_resolution"] = "144 x 200 pixels"
        attributes["supported_formats"] = "PNG, JPG, JPEG"

        return attributes

    @callback
    def _handle_coordinator_update(self) -> None:
        """Mark the image updated when its file or CRC changes."""
        # The server reuses the img_<uid> path, so a re-upload only shows as a
        # new CRC. A failed CRC fetch (None) is not a change.
        image_file = self._get_current_image_file()
        image_crc = self._get_current_image_crc() or self._image_crc
        if (image_file, image_crc) != (self._image_file, self._image_crc):
            self._image_file, self._image_crc = image_file, image_crc
            self._image_bytes = None
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()
