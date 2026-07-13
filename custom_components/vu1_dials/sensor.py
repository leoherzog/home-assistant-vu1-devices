"""Support for VU1 dial sensors."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VU1DialEntity, async_setup_dial_entities
from .config_entities import VU1UpdateModeSensor, VU1BoundEntitySensor

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]

# Diagnostic sensors as (detailed_status data key, translation key) pairs. Each
# instantiates VU1DiagnosticSensorBase directly; unique IDs and translation keys
# derive from these values, so registry entities remain stable.
DIAGNOSTIC_SENSORS: tuple[tuple[str, str], ...] = (
    ("fw_version", "firmware_version"),
    ("hw_version", "hardware_version"),
    ("protocol_version", "protocol_version"),
    ("fw_hash", "firmware_hash"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 sensor entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str, dial_info: dict[str, Any]) -> list:
        return [
            VU1DialSensor(coordinator, dial_uid),
            VU1UpdateModeSensor(coordinator, dial_uid),
            VU1BoundEntitySensor(coordinator, dial_uid),
            VU1ServerNameSensor(coordinator, dial_uid),
            *(
                VU1DiagnosticSensorBase(coordinator, dial_uid, data_key, translation_key)
                for data_key, translation_key in DIAGNOSTIC_SENSORS
            ),
        ]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1DialSensor(VU1DialEntity, CoordinatorEntity, SensorEntity):
    """Representation of a VU1 dial sensor."""

    def __init__(
        self,
        coordinator: "VU1DataUpdateCoordinator",
        dial_uid: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{DOMAIN}_{dial_uid}"
        self._attr_translation_key = "value"

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

        # Add image file info if available
        if "image_file" in dial_data:
            attributes["image_file"] = dial_data["image_file"]

        return attributes


class VU1DiagnosticSensorBase(VU1DialEntity, CoordinatorEntity, SensorEntity):
    """Base class for VU1 diagnostic sensors."""

    def __init__(self, coordinator: "VU1DataUpdateCoordinator", dial_uid: str, data_key: str, translation_key: str) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        # data_key is already a lowercase snake_case API key (e.g. "fw_version").
        self._data_key = data_key
        self._attr_unique_id = f"{dial_uid}_{data_key}"
        self._attr_translation_key = translation_key
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

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


class VU1ServerNameSensor(VU1DialEntity, CoordinatorEntity, SensorEntity):
    """Sensor showing the device name as stored on the VU-Server."""

    def __init__(self, coordinator: "VU1DataUpdateCoordinator", dial_uid: str) -> None:
        """Initialize the server name sensor."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_server_name"
        self._attr_translation_key = "server_name"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the device name from the VU-Server."""
        if not self.coordinator.data:
            return None

        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
        if not dial_data:
            return None

        return dial_data.get("dial_name")
