"""Support for VU1 dial image entities."""
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .vu1_api import VU1APIClient

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 image entities."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client: VU1APIClient = data["client"]

    entities = []
    
    # Create background image entity for each dial
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1DialBackgroundImage(hass, coordinator, client, dial_uid, dial_info))

    async_add_entities(entities)


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
        # Set device class to indicate this is an image-related entity
        self._attr_device_class = "image"
        self._cached_image: bytes | None = None
        self._last_image_file: str | None = None
        self._image_last_updated: datetime | None = None

    @property
    def device_info(self) -> dict[str, Any]:
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
    def state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        attributes = super().state_attributes or {}
        
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

    async def async_update(self) -> None:
        """Update the image entity."""
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