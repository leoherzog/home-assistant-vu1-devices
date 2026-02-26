"""Support for VU1 dial number entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VU1DialEntity, async_setup_dial_entities
from .vu1_api import VU1APIClient
from .config_entities import (
    VU1ValueMinNumber,
    VU1ValueMaxNumber,
    VU1DialEasingPeriodNumber,
    VU1DialEasingStepNumber,
    VU1BacklightEasingPeriodNumber,
    VU1BacklightEasingStepNumber,
)

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 number entities."""
    coordinator = config_entry.runtime_data.coordinator
    client = config_entry.runtime_data.client

    def entity_factory(dial_uid: str, dial_info: dict[str, Any]) -> list:
        return [
            VU1DialNumber(coordinator, client, dial_uid, dial_info),
            VU1ValueMinNumber(coordinator, dial_uid, dial_info),
            VU1ValueMaxNumber(coordinator, dial_uid, dial_info),
            VU1DialEasingPeriodNumber(coordinator, dial_uid, dial_info),
            VU1DialEasingStepNumber(coordinator, dial_uid, dial_info),
            VU1BacklightEasingPeriodNumber(coordinator, dial_uid, dial_info),
            VU1BacklightEasingStepNumber(coordinator, dial_uid, dial_info),
        ]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1DialNumber(VU1DialEntity, CoordinatorEntity, NumberEntity):
    """Representation of a VU1 dial number entity."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: dict[str, Any],
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{DOMAIN}_dial_{dial_uid}"
        self._attr_name = "Value"
        self._attr_has_entity_name = True
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = NumberMode.SLIDER
        self._attr_icon = "mdi:gauge"

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        if not self.coordinator.data:
            return None
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})
        detailed_status = dial_data.get("detailed_status", {})
        value = detailed_status.get("value")
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the dial value."""
        try:
            # First, switch to manual mode if currently in automatic mode
            await self._switch_to_manual_mode_if_needed()

            # Set the dial value
            await self._client.set_dial_value(self._dial_uid, int(value))

            # Optimistically update coordinator data to avoid UI flicker.
            # The VU1 server queues commands and applies them asynchronously,
            # so polling immediately would return stale state.
            if self.coordinator.data:
                dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
                detailed_status = dial_data.get("detailed_status", {})
                detailed_status["value"] = int(value)
            self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error("Failed to set dial value for %s: %s", self._dial_uid, err)
            raise

    async def _switch_to_manual_mode_if_needed(self) -> None:
        """Switch to manual mode if currently in automatic mode."""
        from .device_config import async_get_config_manager
        from .sensor_binding import async_get_binding_manager

        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)

        # Only switch if currently in automatic mode
        if config.get("update_mode") == "automatic":
            _LOGGER.info("Switching dial %s from automatic to manual mode due to manual value change", self._dial_uid)

            # Update configuration to manual mode
            await config_manager.async_update_dial_config(
                self._dial_uid,
                {"update_mode": "manual"}
            )

            # Update sensor bindings to remove the automatic binding
            binding_manager = async_get_binding_manager(self.hass)
            await binding_manager.async_reconfigure_dial_binding(self._dial_uid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {"dial_uid": self._dial_uid}
        dials_data = self.coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._dial_uid, {})

        attributes = {
            "dial_uid": self._dial_uid,
            "dial_name": dial_data.get("dial_name"),
        }

        # Add backlight information from detailed status
        detailed_status = dial_data.get("detailed_status", {})
        backlight = detailed_status.get("backlight", {})
        if backlight:
            attributes.update({
                "backlight_red": backlight.get("red"),
                "backlight_green": backlight.get("green"),
                "backlight_blue": backlight.get("blue"),
            })

        return attributes
