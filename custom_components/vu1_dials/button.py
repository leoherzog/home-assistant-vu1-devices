"""Support for VU1 dial button entities."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
    """Set up VU1 button entities."""
    coordinator = config_entry.runtime_data.coordinator
    client = config_entry.runtime_data.client

    entities = []

    entities.append(VU1ProvisionDialsButton(coordinator, client))

    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1RefreshHardwareInfoButton(coordinator, client, dial_uid, dial_info))
        entities.append(VU1IdentifyDialButton(coordinator, client, dial_uid, dial_info))

    async_add_entities(entities)

    # Register callback for creating entities when new dials are discovered
    async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
        """Create button entities for newly discovered dials."""
        new_entities = []
        for dial_uid, dial_info in new_dials.items():
            _LOGGER.info("Creating button entities for new dial %s", dial_uid)
            new_entities.append(VU1RefreshHardwareInfoButton(coordinator, client, dial_uid, dial_info))
            new_entities.append(VU1IdentifyDialButton(coordinator, client, dial_uid, dial_info))
        if new_entities:
            async_add_entities(new_entities)

    unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
    config_entry.async_on_unload(unsub)


class VU1ProvisionDialsButton(CoordinatorEntity, ButtonEntity):
    """Button to provision new dials detected by the VU1 server."""

    def __init__(self, coordinator, client: VU1APIClient) -> None:
        """Initialize the provision dials button."""
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{coordinator.server_device_identifier}_provision_new_dials"
        self._attr_name = "Provision new dials"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:plus-circle"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the VU1 server."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.server_device_identifier)},
            "manufacturer": "Streacom",
            "model": "VU1 Server",
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            _LOGGER.info("Provisioning new dials via VU1 server")

            # Get dial UIDs before provisioning
            known_dial_uids = self.coordinator.get_known_dial_uids()

            # Call the provision endpoint to detect and add new dials
            result = await self._client.provision_new_dials()

            # Trigger coordinator refresh to discover any newly provisioned dials
            await self.coordinator.async_request_refresh()

            # Check for new dials after refresh
            current_dial_uids = set(self.coordinator.data.get("dials", {}).keys()) if self.coordinator.data else set()
            new_dial_uids = current_dial_uids - known_dial_uids

            if new_dial_uids:
                _LOGGER.info("Discovered %d new dial(s): %s", len(new_dial_uids), new_dial_uids)
                # Update known dials
                self.coordinator.update_known_dials(current_dial_uids)
                # Notify all registered callbacks to create entities for new dials
                await self.coordinator.async_notify_new_dials(new_dial_uids)
            else:
                _LOGGER.info("No new dials discovered during provisioning")

            _LOGGER.info("Successfully provisioned dials: %s", result)

        except Exception as err:
            _LOGGER.error("Failed to provision new dials: %s", err)
            raise

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class VU1RefreshHardwareInfoButton(CoordinatorEntity, ButtonEntity):
    """Button to refresh hardware information for a VU1 dial."""

    def __init__(self, coordinator, client: VU1APIClient, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_refresh_hardware_info"
        self._attr_name = "Refresh hardware info"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:refresh"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {}) if self.coordinator.data else {}
        return get_dial_device_info(self._dial_uid, dial_data, self.coordinator.server_device_identifier)

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            _LOGGER.info("Refreshing hardware info for dial %s", self._dial_uid)
            
            # Call the reload endpoint to get fresh hardware data
            await self._client.reload_dial(self._dial_uid)
            
            # Trigger coordinator refresh to update all entities with new data
            await self.coordinator.async_request_refresh()
            
            _LOGGER.info("Successfully refreshed hardware info for dial %s", self._dial_uid)
            
        except Exception as err:
            _LOGGER.error("Failed to refresh hardware info for dial %s: %s", self._dial_uid, err)
            raise

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._dial_uid in self.coordinator.data.get("dials", {})
        )


class VU1IdentifyDialButton(CoordinatorEntity, ButtonEntity):
    """Button to identify a VU1 dial with white flash animation."""

    def __init__(self, coordinator, client: VU1APIClient, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the identify button."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_identify"
        self._attr_name = "Identify"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_icon = "mdi:lightbulb-on"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {}) if self.coordinator.data else {}
        return get_dial_device_info(self._dial_uid, dial_data, self.coordinator.server_device_identifier)

    async def async_press(self) -> None:
        """Handle the button press - perform identify animation."""
        # Get current backlight state to restore later
        original_backlight = None
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            detailed_status = dial_data.get("detailed_status", {})
            original_backlight = detailed_status.get("backlight", {})
        
        try:
            _LOGGER.info("Starting identify animation for dial %s", self._dial_uid)
            
            # Flash sequence: white for 1s, off for 1s, then restore
            # Step 1: Set to 100% white
            await self._client.set_dial_backlight(self._dial_uid, 100, 100, 100)
            await asyncio.sleep(1.0)
            
            # Step 2: Turn off (0% all colors) 
            await self._client.set_dial_backlight(self._dial_uid, 0, 0, 0)
            await asyncio.sleep(1.0)
            
            # Step 3: Restore original state
            if original_backlight:
                red = original_backlight.get("red", 0)
                green = original_backlight.get("green", 0) 
                blue = original_backlight.get("blue", 0)
                await self._client.set_dial_backlight(self._dial_uid, red, green, blue)
            else:
                # Default to off if no original state
                await self._client.set_dial_backlight(self._dial_uid, 0, 0, 0)
            
            # Refresh coordinator to update UI state
            await asyncio.sleep(0.1)  # Small delay for hardware to settle
            await self.coordinator.async_request_refresh()
            
            _LOGGER.info("Completed identify animation for dial %s", self._dial_uid)
            
        except Exception as err:
            _LOGGER.error("Failed to perform identify animation for dial %s: %s", self._dial_uid, err)
            # Try to restore original state on error
            if original_backlight:
                try:
                    red = original_backlight.get("red", 0)
                    green = original_backlight.get("green", 0)
                    blue = original_backlight.get("blue", 0) 
                    await self._client.set_dial_backlight(self._dial_uid, red, green, blue)
                    await self.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.error("Failed to restore original backlight state for dial %s", self._dial_uid)
            raise

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._dial_uid in self.coordinator.data.get("dials", {})
        )