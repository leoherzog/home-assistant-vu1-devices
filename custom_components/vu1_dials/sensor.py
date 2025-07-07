"""Support for VU1 dial sensors."""
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
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
        
        # Initialize _last_known_name from current coordinator data to ensure
        # accurate state tracking even after entity recreation/restart
        current_dial_data = None
        if coordinator.data and coordinator.data.get("dials"):
            current_dial_data = coordinator.data["dials"].get(dial_uid)
        
        if current_dial_data and current_dial_data.get("dial_name"):
            self._last_known_name = current_dial_data["dial_name"]
        else:
            # Fallback to the name from dial_data parameter
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
        
        # Set up registry update listeners for bidirectional name sync
        # Use async_track_entity_registry_updated_event for entity changes
        self._entity_registry_updated_unsub = er.async_track_entity_registry_updated_event(
            self.hass, self.entity_id, self._async_entity_registry_updated
        )
        
        # For device registry updates, we need to track by device id
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self._dial_uid)})
        if device:
            self._device_registry_updated_unsub = dr.async_track_device_registry_updated_event(
                self.hass, device.id, self._async_device_registry_updated
            )

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from hass."""
        await super().async_will_remove_from_hass()
        
        # Clean up registry update listeners
        if self._entity_registry_updated_unsub:
            self._entity_registry_updated_unsub()
            self._entity_registry_updated_unsub = None
        if self._device_registry_updated_unsub:
            self._device_registry_updated_unsub()
            self._device_registry_updated_unsub = None

    @callback
    def _async_entity_registry_updated(self, event: Event) -> None:
        """Handle entity registry updates for name sync."""
        if not event.data:
            return
            
        changes = event.data.get("changes", {})
        _LOGGER.debug("Entity registry update for %s: changes=%s", self.entity_id, changes)
        
        # Check if this is a name change
        if "name" in changes or "original_name" in changes:
            # Schedule the async operation
            self.hass.async_create_task(self._handle_entity_name_change())

    async def _handle_entity_name_change(self) -> None:
        """Handle entity name change asynchronously."""
        # Get new name from registry
        entity_registry = er.async_get(self.hass)
        entry = entity_registry.async_get(self.entity_id)
        
        if not entry:
            return
            
        # Determine the actual name to use
        new_name = entry.name
        if not new_name:
            # If no custom name, check if original_name was updated
            new_name = entry.original_name
            
        if not new_name:
            return
            
        # Only sync if name actually changed and not in grace period
        if new_name != self._last_known_name and not self.coordinator.is_in_grace_period(self._dial_uid):
            _LOGGER.info("Entity name changed for %s: %s -> %s", self._dial_uid, self._last_known_name, new_name)
            
            # Mark grace period BEFORE attempting sync to prevent race conditions
            self.coordinator.mark_name_change_from_ha(self._dial_uid)
            
            # Attempt atomic name sync
            success = await self._sync_name_to_server(new_name)
            if success:
                # Only update local state if server sync succeeded
                self._last_known_name = new_name

    @callback
    def _async_device_registry_updated(self, event: Event) -> None:
        """Handle device registry updates for name sync."""
        if not event.data:
            return
            
        changes = event.data.get("changes", {})
        _LOGGER.debug("Device registry update for dial %s: changes=%s", self._dial_uid, changes)
        
        # Check if name changed
        if "name" in changes:
            # Schedule the async operation
            self.hass.async_create_task(self._handle_device_name_change())

    async def _handle_device_name_change(self) -> None:
        """Handle device name change asynchronously."""
        # Get device from registry
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self._dial_uid)})
        
        if not device or not device.name:
            return
            
        # Only sync if name actually changed and not in grace period
        if device.name != self._last_known_name and not self.coordinator.is_in_grace_period(self._dial_uid):
            _LOGGER.info("Device name changed for %s: %s -> %s", self._dial_uid, self._last_known_name, device.name)
            
            # Mark grace period BEFORE attempting sync to prevent race conditions
            self.coordinator.mark_name_change_from_ha(self._dial_uid)
            
            # Attempt atomic name sync
            success = await self._sync_name_to_server(device.name)
            if success:
                # Only update local state if server sync succeeded
                self._last_known_name = device.name

    async def _sync_name_to_server(self, name: str) -> bool:
        """Sync the entity name to the VU1 server.
        
        Returns:
            bool: True if sync succeeded, False otherwise
        """
        try:
            # Note: Grace period is now marked by the caller before this method
            await self._client.set_dial_name(self._dial_uid, name)
            # Trigger coordinator refresh to update all related entities
            await self.coordinator.async_request_refresh()
            _LOGGER.info("Synced dial name '%s' to VU1 server for %s", name, self._dial_uid)
            return True
        except Exception as err:
            _LOGGER.error("Failed to sync dial name to server for %s: %s", self._dial_uid, err)
            return False

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

    async def async_set_dial_name(self, name: str) -> bool:
        """Set the dial name.
        
        Returns:
            bool: True if name setting succeeded, False otherwise
        """
        # Mark grace period to prevent sync loop
        self.coordinator.mark_name_change_from_ha(self._dial_uid)
        
        try:
            await self._client.set_dial_name(self._dial_uid, name)
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
            _LOGGER.info("Entity method: synced dial name '%s' to VU1 server for %s", name, self._dial_uid)
            return True
        except Exception as err:
            _LOGGER.error("Entity method failed to set dial name for %s: %s", self._dial_uid, err)
            return False

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


