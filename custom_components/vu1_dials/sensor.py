"""Support for VU1 dial sensors."""
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .vu1_api import VU1APIClient
from .config_entities import VU1UpdateModeSensor, VU1BoundEntitySensor

if TYPE_CHECKING:
    from . import VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 sensor entities."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client: VU1APIClient = data["client"]

    entities = []
    
    # Create sensor entities for each dial
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        # Main dial sensor
        entities.append(VU1DialSensor(coordinator, client, dial_uid, dial_info))
        
        # Configuration status sensors
        entities.extend([
            VU1UpdateModeSensor(coordinator, dial_uid, dial_info),
            VU1BoundEntitySensor(coordinator, dial_uid, dial_info),
        ])
        
        # Diagnostic sensors (disabled by default)
        entities.extend([
            VU1FirmwareVersionSensor(coordinator, dial_uid, dial_info),
            VU1HardwareVersionSensor(coordinator, dial_uid, dial_info),
            VU1ProtocolVersionSensor(coordinator, dial_uid, dial_info),
            VU1FirmwareHashSensor(coordinator, dial_uid, dial_info),
        ])

    async_add_entities(entities)


class VU1DialSensor(CoordinatorEntity, SensorEntity):
    """Representation of a VU1 dial sensor."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: Dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{DOMAIN}_{dial_uid}"
        self._attr_name = dial_data.get("dial_name", f"VU1 Dial {dial_uid}")
        self._attr_has_entity_name = True
        self._entity_registry_updated_unsub = None
        self._device_registry_updated_unsub = None
        self._last_known_name = self._attr_name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_name = f"VU1 Dial {self._dial_uid}"
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            dial_name = dial_data.get("dial_name", dial_name)
        
        return DeviceInfo(
            identifiers={(DOMAIN, self._dial_uid)},
            name=dial_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version="1.0",
            # Add via_device to link to the VU1 server hub
            via_device=(DOMAIN, self.coordinator.server_device_id),
        )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()

    @callback
    def _async_entity_registry_updated(self, event) -> None:
        """Handle entity registry update."""
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self.entity_id)
        
        if entity_entry:
            # Check if entity has a custom name or if it was reset to default
            if entity_entry.name:
                # Entity has a custom name set by user
                new_name = entity_entry.name
            else:
                # Name was reset to default - use device name
                device_registry = dr.async_get(self.hass)
                device = device_registry.async_get_device(identifiers={(DOMAIN, self._dial_uid)})
                if device and device.name_by_user:
                    new_name = device.name_by_user
                else:
                    # Fall back to server's dial name
                    dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
                    new_name = dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}")
            
            if new_name != self._last_known_name:
                self._last_known_name = new_name
                self.hass.async_create_task(self._sync_name_to_server(new_name))

    @callback
    def _async_device_registry_updated(self, event) -> None:
        """Handle device registry update."""
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get(event.data["device_id"])
        
        if device and device.name_by_user:
            # Device has a custom name set by user
            new_name = device.name_by_user
            if new_name != self._last_known_name:
                self._last_known_name = new_name
                self.hass.async_create_task(self._sync_name_to_server(new_name))

    async def _sync_name_to_server(self, name: str) -> None:
        """Sync the entity name to the VU1 server."""
        try:
            # Mark grace period to prevent sync loop
            self.coordinator.mark_name_change_from_ha(self._dial_uid)
            
            await self._client.set_dial_name(self._dial_uid, name)
            # Trigger coordinator refresh to update all related entities
            await self.coordinator.async_request_refresh()
            _LOGGER.info("Synced dial name '%s' to VU1 server for %s", name, self._dial_uid)
        except Exception as err:
            _LOGGER.error("Failed to sync dial name to server for %s: %s", self._dial_uid, err)

    @property
    def native_value(self) -> Optional[int]:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            _LOGGER.debug("No coordinator data available for %s", self._dial_uid)
            return None
            
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        if not dial_data:
            _LOGGER.debug("No dial data for %s", self._dial_uid)
            return None
            
        detailed_status = dial_data.get("detailed_status", {})
        return detailed_status.get("value")

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "%"

    @property
    def device_class(self) -> Optional[SensorDeviceClass]:
        """Return the device class."""
        # Using generic device class since this is a dial/gauge
        return None

    @property
    def state_class(self) -> Optional[SensorStateClass]:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> str:
        """Return the icon for the sensor."""
        return "mdi:gauge"

    @property
    def should_poll(self) -> bool:
        """No polling needed, we use coordinator."""
        return False

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        attributes = {
            "dial_uid": self._dial_uid,
        }
        
        if not self.coordinator.data:
            _LOGGER.debug("No coordinator data available for attributes on %s", self._dial_uid)
            return attributes
            
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        if not dial_data:
            _LOGGER.debug("No dial data available for attributes on %s", self._dial_uid)
            return attributes
        
        attributes["dial_name"] = dial_data.get("dial_name")

        # Add backlight information from detailed status
        detailed_status = dial_data.get("detailed_status", {})
        backlight = detailed_status.get("backlight", {})
        if backlight:
            attributes.update({
                "backlight_red": backlight.get("red"),
                "backlight_green": backlight.get("green"),
                "backlight_blue": backlight.get("blue"),
            })

        # Add image file information
        if "image_file" in dial_data:
            attributes["image_file"] = dial_data["image_file"]

        # Add detailed status if available
        if detailed_status:
            attributes["detailed_status"] = detailed_status

        return attributes

    async def async_set_dial_value(self, value: int) -> None:
        """Set the dial value."""
        try:
            await self._client.set_dial_value(self._dial_uid, value)
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set dial value for %s: %s", self._dial_uid, err)

    async def async_set_dial_backlight(self, red: int, green: int, blue: int) -> None:
        """Set the dial backlight."""
        try:
            await self._client.set_dial_backlight(self._dial_uid, red, green, blue)
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set dial backlight for %s: %s", self._dial_uid, err)

    async def async_set_dial_name(self, name: str) -> None:
        """Set the dial name."""
        try:
            # Mark grace period to prevent sync loop
            self.coordinator.mark_name_change_from_ha(self._dial_uid)
            
            await self._client.set_dial_name(self._dial_uid, name)
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set dial name for %s: %s", self._dial_uid, err)

    async def async_reload_dial(self) -> None:
        """Reload the dial configuration."""
        try:
            await self._client.reload_dial(self._dial_uid)
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to reload dial %s: %s", self._dial_uid, err)

    async def async_calibrate_dial(self) -> None:
        """Calibrate the dial."""
        try:
            await self._client.calibrate_dial(self._dial_uid)
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to calibrate dial %s: %s", self._dial_uid, err)


class VU1DiagnosticSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for VU1 diagnostic sensors."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any], attr_name: str, sensor_name: str) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_name = attr_name
        self._attr_unique_id = f"{dial_uid}_{attr_name.lower().replace(' ', '_')}"
        self._attr_name = sensor_name
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False
        self._attr_icon = "mdi:information"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_name = f"VU1 Dial {self._dial_uid}"
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            dial_name = dial_data.get("dial_name", dial_name)
        
        return DeviceInfo(
            identifiers={(DOMAIN, self._dial_uid)},
            name=dial_name,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version="1.0",
            via_device=(DOMAIN, self.coordinator.server_device_id),
        )

    @property
    def native_value(self) -> Optional[str]:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
            
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        if not dial_data:
            return None
            
        detailed_status = dial_data.get("detailed_status", {})
        return detailed_status.get(self._attr_name)

    @property
    def should_poll(self) -> bool:
        """No polling needed, we use coordinator."""
        return False


class VU1FirmwareVersionSensor(VU1DiagnosticSensorBase):
    """Sensor for firmware version."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the firmware version sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "fw_version", "Firmware version")
        self._attr_icon = "mdi:chip"


class VU1HardwareVersionSensor(VU1DiagnosticSensorBase):
    """Sensor for hardware version."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the hardware version sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "hw_version", "Hardware version")
        self._attr_icon = "mdi:developer-board"


class VU1ProtocolVersionSensor(VU1DiagnosticSensorBase):
    """Sensor for protocol version."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the protocol version sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "protocol_version", "Protocol version")
        self._attr_icon = "mdi:api"


class VU1FirmwareHashSensor(VU1DiagnosticSensorBase):
    """Sensor for firmware hash."""

    def __init__(self, coordinator, dial_uid: str, dial_data: Dict[str, Any]) -> None:
        """Initialize the firmware hash sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "fw_hash", "Firmware hash")
        self._attr_icon = "mdi:fingerprint"


