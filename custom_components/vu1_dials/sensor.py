"""Support for VU1 dial sensors."""
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, get_dial_device_info
from .vu1_api import VU1APIClient
from .config_entities import VU1UpdateModeSensor, VU1BoundEntitySensor

if TYPE_CHECKING:
    from . import VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


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
    
    dial_data = coordinator.data.get("dials", {})
    for dial_uid, dial_info in dial_data.items():
        entities.append(VU1DialSensor(coordinator, client, dial_uid, dial_info))
        
        entities.extend([
            VU1UpdateModeSensor(coordinator, dial_uid, dial_info),
            VU1BoundEntitySensor(coordinator, dial_uid, dial_info),
        ])
        
        entities.extend([
            VU1FirmwareVersionSensor(coordinator, dial_uid, dial_info),
            VU1HardwareVersionSensor(coordinator, dial_uid, dial_info),
            VU1ProtocolVersionSensor(coordinator, dial_uid, dial_info),
            VU1FirmwareHashSensor(coordinator, dial_uid, dial_info),
        ])

    async_add_entities(entities)

    # Register callback for creating entities when new dials are discovered
    async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
        """Create sensor entities for newly discovered dials."""
        new_entities = []
        for dial_uid, dial_info in new_dials.items():
            _LOGGER.info("Creating sensor entities for new dial %s", dial_uid)
            new_entities.append(VU1DialSensor(coordinator, client, dial_uid, dial_info))
            new_entities.extend([
                VU1UpdateModeSensor(coordinator, dial_uid, dial_info),
                VU1BoundEntitySensor(coordinator, dial_uid, dial_info),
                VU1FirmwareVersionSensor(coordinator, dial_uid, dial_info),
                VU1HardwareVersionSensor(coordinator, dial_uid, dial_info),
                VU1ProtocolVersionSensor(coordinator, dial_uid, dial_info),
                VU1FirmwareHashSensor(coordinator, dial_uid, dial_info),
            ])
        if new_entities:
            async_add_entities(new_entities)

    unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
    config_entry.async_on_unload(unsub)


class VU1DialSensor(CoordinatorEntity, SensorEntity):
    """Representation of a VU1 dial sensor."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{DOMAIN}_{dial_uid}"
        self._attr_name = dial_data.get("dial_name", f"VU1 Dial {dial_uid}")
        self._attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_data = {}
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        return get_dial_device_info(
            self._dial_uid, dial_data, self.coordinator.server_device_identifier
        )

    @property
    def native_value(self) -> int | None:
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
    def state_class(self) -> SensorStateClass | None:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def icon(self) -> str:
        """Return the icon for the sensor."""
        return "mdi:gauge"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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

        # Add backlight color information from detailed status
        detailed_status = dial_data.get("detailed_status", {})
        backlight = detailed_status.get("backlight", {})
        if backlight:
            attributes.update({
                "backlight_red": backlight.get("red"),
                "backlight_green": backlight.get("green"),
                "backlight_blue": backlight.get("blue"),
            })

        # Add image file info if available
        if "image_file" in dial_data:
            attributes["image_file"] = dial_data["image_file"]

        # Include full detailed status for advanced users
        if detailed_status:
            attributes["detailed_status"] = detailed_status

        return attributes


class VU1DiagnosticSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for VU1 diagnostic sensors."""

    def __init__(self, coordinator, dial_uid: str, dial_data: dict[str, Any], attr_name: str, sensor_name: str) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._data_key = attr_name  # Store API lookup key separately
        self._attr_unique_id = f"{dial_uid}_{attr_name.lower().replace(' ', '_')}"
        self._attr_name = sensor_name
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False
        self._attr_icon = "mdi:information"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_data = {}
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        return get_dial_device_info(
            self._dial_uid, dial_data, self.coordinator.server_device_identifier
        )

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None

        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        if not dial_data:
            return None

        detailed_status = dial_data.get("detailed_status", {})
        return detailed_status.get(self._data_key)


class VU1FirmwareVersionSensor(VU1DiagnosticSensorBase):
    """Sensor for firmware version."""

    def __init__(self, coordinator, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the firmware version sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "fw_version", "Firmware version")
        self._attr_icon = "mdi:chip"


class VU1HardwareVersionSensor(VU1DiagnosticSensorBase):
    """Sensor for hardware version."""

    def __init__(self, coordinator, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the hardware version sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "hw_version", "Hardware version")
        self._attr_icon = "mdi:developer-board"


class VU1ProtocolVersionSensor(VU1DiagnosticSensorBase):
    """Sensor for protocol version."""

    def __init__(self, coordinator, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the protocol version sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "protocol_version", "Protocol version")
        self._attr_icon = "mdi:api"


class VU1FirmwareHashSensor(VU1DiagnosticSensorBase):
    """Sensor for firmware hash."""

    def __init__(self, coordinator, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the firmware hash sensor."""
        super().__init__(coordinator, dial_uid, dial_data, "fw_hash", "Firmware hash")
        self._attr_icon = "mdi:fingerprint"
