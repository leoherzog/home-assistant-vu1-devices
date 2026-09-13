"""Support for VU1 dial button entities."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import VU1DialEntity, async_setup_dial_entities

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VU1 button entities."""
    coordinator = config_entry.runtime_data.coordinator

    # Server-level entity (not per-dial) — added separately
    async_add_entities([VU1ProvisionDialsButton(coordinator)])

    def entity_factory(dial_uid: str) -> list:
        return [
            VU1RefreshHardwareInfoButton(coordinator, dial_uid),
            VU1IdentifyDialButton(coordinator, dial_uid),
        ]

    async_setup_dial_entities(config_entry, async_add_entities, entity_factory)


class VU1ProvisionDialsButton(CoordinatorEntity["VU1DataUpdateCoordinator"], ButtonEntity):
    """Button to provision new dials detected by the VU1 server."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator) -> None:
        """Initialize the provision dials button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.server_device_identifier}_provision_new_dials"
        self._attr_translation_key = "provision_new_dials"
        # Not a VU1DialEntity, so set has_entity_name here.
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the VU1 server."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.server_device_identifier)},
            manufacturer="Streacom",
            model="VU1 Server",
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Provisioning new dials via VU1 server")
        await self.coordinator.client.provision_new_dials()
        # Await an immediate refresh (not the debounced async_request_refresh).
        # Each platform's coordinator listener adds entities for new dials.
        await self.coordinator.async_refresh()


class VU1RefreshHardwareInfoButton(VU1DialEntity, ButtonEntity):
    """Button to refresh hardware information for a VU1 dial."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_refresh_hardware_info"
        self._attr_translation_key = "refresh_hardware_info"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.client.reload_dial(self._dial_uid)
        await self.coordinator.async_request_refresh()


class VU1IdentifyDialButton(VU1DialEntity, ButtonEntity):
    """Button to identify a VU1 dial with white flash animation."""

    _attr_device_class = ButtonDeviceClass.IDENTIFY

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the identify button."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_identify"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Flash the backlight white for a second, then restore it."""
        original = self._backlight()
        await self.coordinator.client.set_dial_backlight(self._dial_uid, 100, 100, 100, 0)

        async def _finish_identify() -> None:
            try:
                try:
                    await asyncio.sleep(1.0)
                finally:
                    await self.coordinator.async_set_backlight(self._dial_uid, *original)
            except HomeAssistantError as err:
                _LOGGER.warning("Failed to restore backlight for dial %s: %s", self._dial_uid, err)

        self.coordinator.config_entry.async_create_background_task(
            self.hass, _finish_identify(), name=f"vu1_identify_{self._dial_uid}"
        )
