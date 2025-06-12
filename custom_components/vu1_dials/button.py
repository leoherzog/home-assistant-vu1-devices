"""Support for VU1 dial button entities."""
import logging
from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .vu1_api import VU1APIClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 button entities."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client: VU1APIClient = data["client"]

    entities = []
    
    # Create provision new dials button for server device
    entities.append(VU1ProvisionDialsButton(coordinator, client))
    
    # Create refresh button for each dial
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1RefreshHardwareInfoButton(coordinator, client, dial_uid, dial_info))

    async_add_entities(entities)


class VU1ProvisionDialsButton(CoordinatorEntity, ButtonEntity):
    """Button to provision new dials detected by the VU1 server."""

    def __init__(self, coordinator, client: VU1APIClient) -> None:
        """Initialize the provision dials button."""
        super().__init__(coordinator)
        self._client = client
        self._attr_unique_id = f"{coordinator.server_device_id}_provision_new_dials"
        self._attr_name = "Provision new dials"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_icon = "mdi:plus-circle"

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information for the VU1 server."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.server_device_id)},
            "name": self.coordinator.server_device_name,
            "manufacturer": "Streacom",
            "model": "VU1 Server",
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            _LOGGER.info("Provisioning new dials via VU1 server")
            
            # Call the provision endpoint to detect and add new dials
            result = await self._client.provision_new_dials()
            
            # Trigger coordinator refresh to discover any newly provisioned dials
            await self.coordinator.async_request_refresh()
            
            _LOGGER.info("Successfully provisioned new dials: %s", result)
            
        except Exception as err:
            _LOGGER.error("Failed to provision new dials: %s", err)
            raise

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class VU1RefreshHardwareInfoButton(CoordinatorEntity, ButtonEntity):
    """Button to refresh hardware information for a VU1 dial."""

    def __init__(self, coordinator, client: VU1APIClient, dial_uid: str, dial_data: Dict[str, Any]) -> None:
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
    def device_info(self) -> Dict[str, Any]:
        """Return device information."""
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        return {
            "identifiers": {(DOMAIN, self._dial_uid)},
            "name": dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}"),
            "manufacturer": "Streacom",
            "model": "VU1 Dial",
            "via_device": (DOMAIN, self.coordinator.server_device_id),
        }

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