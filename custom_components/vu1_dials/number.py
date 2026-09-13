"""Support for VU1 dial number entities."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .config_entities import CONFIG_NUMBER_DESCRIPTIONS, VU1ConfigNumber
from .const import DOMAIN
from .entity import VU1DialEntity, async_setup_dial_entities
from .sensor_binding import async_switch_to_manual

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

__all__ = ["async_setup_entry"]

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VU1 number entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str) -> list:
        return [
            VU1DialNumber(coordinator, dial_uid),
            *(
                VU1ConfigNumber(coordinator, dial_uid, description)
                for description in CONFIG_NUMBER_DESCRIPTIONS
            ),
        ]

    async_setup_dial_entities(config_entry, async_add_entities, entity_factory)


class VU1DialNumber(VU1DialEntity, NumberEntity):
    """Representation of a VU1 dial number entity."""

    def __init__(
        self,
        coordinator: VU1DataUpdateCoordinator,
        dial_uid: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{DOMAIN}_dial_{dial_uid}"
        # Main-feature convention: the dial value is the device's primary
        # entity, so it inherits the device name (no per-entity suffix).
        # _attr_has_entity_name is inherited from VU1DialEntity.
        self._attr_name = None
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = NumberMode.SLIDER
        self._attr_translation_key = "dial_value"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        value = self.dial_data.get("detailed_status", {}).get("value")
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the dial value."""
        await async_switch_to_manual(self.hass, self._dial_uid)
        await self.coordinator.client.set_dial_value(self._dial_uid, int(value))

        if dial := self.coordinator.data["dials"].get(self._dial_uid):
            dial["detailed_status"]["value"] = int(value)
            self.coordinator.async_update_listeners()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {"dial_uid": self._dial_uid, "dial_name": self.dial_data.get("dial_name")}
