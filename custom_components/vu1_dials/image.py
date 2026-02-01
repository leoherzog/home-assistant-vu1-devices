"""Support for VU1 dial image entities."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, get_dial_device_info
from .vu1_api import VU1APIClient

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
    client = config_entry.runtime_data.client

    entities = []
    
    # Create background image entity for each dial
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1DialBackgroundImage(hass, coordinator, client, dial_uid, dial_info))

    async_add_entities(entities)

    # Register callback for creating entities when new dials are discovered
    async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
        """Create image entities for newly discovered dials."""
        new_entities = []
        for dial_uid, dial_info in new_dials.items():
            _LOGGER.info("Creating image entity for new dial %s", dial_uid)
            new_entities.append(VU1DialBackgroundImage(hass, coordinator, client, dial_uid, dial_info))
        if new_entities:
            async_add_entities(new_entities)

    unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
    config_entry.async_on_unload(unsub)


class VU1DialBackgroundImage(CoordinatorEntity, ImageEntity):
    """Image entity showing the current background image of a VU1 dial."""

    def __init__(self, hass: HomeAssistant, coordinator, client: VU1APIClient, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the dial background image entity."""
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_background_image"
        self._attr_name = "Background image"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:image"
        self._cached_image: bytes | None = None
        self._last_image_file: str | None = None
        self._image_last_updated: datetime | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {}) if self.coordinator.data else {}
        return get_dial_device_info(self._dial_uid, dial_data, self.coordinator.server_device_identifier)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._dial_uid in self.coordinator.data.get("dials", {})
        )

    async def async_image(self) -> bytes | None:
        """Return the current dial background image."""
        try:
            # Check if we need to fetch a new image
            current_image_file = self._get_current_image_file()
            
            # If no image file is set, return None
            if not current_image_file:
                _LOGGER.debug("No image file set for dial %s", self._dial_uid)
                return None
            
            # Check if we need to fetch new image data
            if (self._cached_image is None or 
                current_image_file != self._last_image_file):
                
                _LOGGER.info("Fetching background image for dial %s", self._dial_uid)
                
                # Fetch image from VU1 server
                image_data = await self._client.get_dial_image(self._dial_uid)
                
                if image_data:
                    self._cached_image = image_data
                    self._last_image_file = current_image_file
                    self._image_last_updated = dt_util.utcnow()
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

    @property
    def content_type(self) -> str:
        """Return the content type of the image."""
        # VU1 dials support PNG and JPEG, but we'll default to PNG
        # The actual content type could be determined from the image data
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
                self._image_last_updated = None

            # Also check if image file path changed
            current_image_file = self._get_current_image_file()
            if current_image_file and current_image_file != self._last_image_file:
                _LOGGER.debug("Image file path changed for dial %s, clearing cache", self._dial_uid)
                self._cached_image = None
                self._image_last_updated = None

        # Call parent to trigger state update
        super()._handle_coordinator_update()
