"""Support for VU1 dial image entities."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import VU1DialEntity, async_setup_dial_entities

if TYPE_CHECKING:
    from . import VU1ConfigEntry

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 image entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str, dial_info: dict[str, Any]) -> list:
        return [VU1DialBackgroundImage(hass, coordinator, dial_uid)]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1DialBackgroundImage(VU1DialEntity, CoordinatorEntity, ImageEntity):
    """Image entity showing the current background image of a VU1 dial."""

    def __init__(self, hass: HomeAssistant, coordinator, dial_uid: str) -> None:
        """Initialize the dial background image entity."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_background_image"
        self._attr_translation_key = "background_image"
        self._cached_image: bytes | None = None
        self._last_image_file: str | None = None
        self._last_image_crc: int | None = None
        self._image_last_updated: datetime | None = None
        self._content_type: str | None = None

    async def async_image(self) -> bytes | None:
        """Return the current dial background image."""
        try:
            # Check if we need to fetch a new image
            current_image_file = self._get_current_image_file()
            current_image_crc = self._get_current_image_crc()

            # If no image file is set, return None
            if not current_image_file:
                _LOGGER.debug("No image file set for dial %s", self._dial_uid)
                return None

            # Check if we need to fetch new image data. The server always writes
            # to the same img_{uid} path, so the CRC is the reliable signal that
            # a re-uploaded background has actually changed.
            if (self._cached_image is None or
                current_image_file != self._last_image_file or
                current_image_crc != self._last_image_crc):

                _LOGGER.info("Fetching background image for dial %s", self._dial_uid)

                # Fetch image from VU1 server
                image_data = await self.coordinator.client.get_dial_image(self._dial_uid)

                if image_data:
                    self._cached_image = image_data
                    self._last_image_file = current_image_file
                    self._last_image_crc = current_image_crc
                    self._image_last_updated = dt_util.utcnow()
                    self._content_type = self._sniff_content_type(image_data)
                    _LOGGER.debug("Successfully fetched image for dial %s (%d bytes)",
                                self._dial_uid, len(image_data))
                else:
                    _LOGGER.warning("No image data returned for dial %s", self._dial_uid)
                    return None

            return self._cached_image

        except Exception as err:
            _LOGGER.error("Failed to fetch image for dial %s: %s", self._dial_uid, err)
            return None

    def _get_current_image_file(self) -> str | None:
        """Get the current image file path from coordinator data."""
        if not self.coordinator.data:
            return None

        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        if not dial_data:
            return None

        # Check dial data first
        image_file = dial_data.get("image_file")
        if image_file:
            return image_file

        # Fallback to detailed status
        detailed_status = dial_data.get("detailed_status", {})
        return detailed_status.get("image_file")

    def _get_current_image_crc(self) -> int | None:
        """Get the current image CRC from coordinator data."""
        if not self.coordinator.data:
            return None

        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        return dial_data.get("image_crc")

    @staticmethod
    def _sniff_content_type(image_data: bytes) -> str:
        """Determine the image content type from its magic bytes."""
        if image_data.startswith(b"\xff\xd8"):
            return "image/jpeg"
        # PNG signature, and the safe default for VU1 dial faces.
        return "image/png"

    @property
    def content_type(self) -> str:
        """Return the content type of the image."""
        return self._content_type or "image/png"

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
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            detailed_status = dial_data.get("detailed_status", {})
            if "image_changed" in detailed_status:
                attributes["image_changed"] = detailed_status["image_changed"]

        # Add technical specifications
        attributes["display_resolution"] = "144 x 200 pixels"
        attributes["supported_formats"] = "PNG, JPG, JPEG"

        return attributes

    @property
    def image_last_updated(self) -> datetime | None:
        """Return when the image was last updated."""
        return self._image_last_updated

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Check if image has changed according to server
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            detailed_status = dial_data.get("detailed_status", {})

            # If server indicates image changed, clear cache to force refresh
            if detailed_status.get("image_changed", False):
                _LOGGER.debug("Server indicates image changed for dial %s, clearing cache", self._dial_uid)
                self._cached_image = None
                self._last_image_file = None
                # Signal a fresh image (not None, which would read as unknown)
                # so picture cards refetch instead of showing a broken image.
                self._image_last_updated = dt_util.utcnow()

            # The CRC is the reliable change signal: the server clears
            # image_changed within ~1s and always reuses the same image_file
            # path, so a re-uploaded background only shows up as a new CRC.
            current_image_crc = self._get_current_image_crc()
            if current_image_crc is not None and current_image_crc != self._last_image_crc:
                _LOGGER.debug("Image CRC changed for dial %s, clearing cache", self._dial_uid)
                self._cached_image = None
                self._image_last_updated = dt_util.utcnow()

            # Also check if image file path changed
            current_image_file = self._get_current_image_file()
            if current_image_file and current_image_file != self._last_image_file:
                _LOGGER.debug("Image file path changed for dial %s, clearing cache", self._dial_uid)
                self._cached_image = None
                self._image_last_updated = dt_util.utcnow()

        # Call parent to trigger state update
        super()._handle_coordinator_update()
