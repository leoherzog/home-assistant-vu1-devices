"""Support for VU1 dial sensors."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .config_entities import VU1BoundEntitySensor, VU1UpdateModeSensor
from .entity import VU1DialEntity, async_setup_dial_entities

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

__all__ = ["async_setup_entry"]

PARALLEL_UPDATES = 0

# Diagnostic sensors as (detailed_status data key, translation key) pairs.
DIAGNOSTIC_SENSORS: tuple[tuple[str, str], ...] = (
    ("protocol_version", "protocol_version"),
    ("fw_hash", "firmware_hash"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VU1 sensor entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str) -> list:
        return [
            VU1UpdateModeSensor(coordinator, dial_uid),
            VU1BoundEntitySensor(coordinator, dial_uid),
            VU1ServerNameSensor(coordinator, dial_uid),
            *(
                VU1DiagnosticSensorBase(coordinator, dial_uid, data_key, translation_key)
                for data_key, translation_key in DIAGNOSTIC_SENSORS
            ),
        ]

    async_setup_dial_entities(config_entry, async_add_entities, entity_factory)


class VU1DiagnosticSensorBase(VU1DialEntity, SensorEntity):
    """Base class for VU1 diagnostic sensors."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str, data_key: str, translation_key: str) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator, dial_uid)
        self._data_key = data_key
        self._attr_unique_id = f"{dial_uid}_{data_key}"
        self._attr_translation_key = translation_key
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        return self.dial_data.get("detailed_status", {}).get(self._data_key)


class VU1ServerNameSensor(VU1DialEntity, SensorEntity):
    """Sensor showing the device name as stored on the VU-Server."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the server name sensor."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_server_name"
        self._attr_translation_key = "server_name"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the device name from the VU-Server."""
        return self.dial_data.get("dial_name")
