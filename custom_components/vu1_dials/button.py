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

from .const import DOMAIN, VU1DialEntity, async_setup_dial_entities

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

    # Server-level entity (not per-dial) — added separately
    async_add_entities([VU1ProvisionDialsButton(coordinator)])

    def entity_factory(dial_uid: str, dial_info: dict[str, Any]) -> list:
        return [
            VU1RefreshHardwareInfoButton(coordinator, dial_uid),
            VU1IdentifyDialButton(coordinator, dial_uid),
        ]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1ProvisionDialsButton(CoordinatorEntity, ButtonEntity):
    """Button to provision new dials detected by the VU1 server."""

    def __init__(self, coordinator) -> None:
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
        try:
            _LOGGER.info("Provisioning new dials via VU1 server")

            # Call the provision endpoint to detect and add new dials
            result = await self.coordinator.client.provision_new_dials()

            # Await an immediate refresh (not the debounced async_request_refresh,
            # which only schedules within the 10s window). The coordinator's
            # _async_update_data owns new-dial detection: it diffs against the
            # known set and schedules async_notify_new_dials itself. Doing that
            # diff/notify here too would double-fire the callbacks for the same
            # dials and log "unique id already exists" warnings, so we rely
            # solely on the coordinator path.
            await self.coordinator.async_refresh()

            _LOGGER.info("Successfully provisioned dials: %s", result)

        except Exception as err:
            _LOGGER.error("Failed to provision new dials: %s", err)
            raise


class VU1RefreshHardwareInfoButton(VU1DialEntity, CoordinatorEntity, ButtonEntity):
    """Button to refresh hardware information for a VU1 dial."""

    def __init__(self, coordinator, dial_uid: str) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_refresh_hardware_info"
        self._attr_translation_key = "refresh_hardware_info"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            _LOGGER.info("Refreshing hardware info for dial %s", self._dial_uid)

            # Call the reload endpoint to get fresh hardware data
            await self.coordinator.client.reload_dial(self._dial_uid)

            # Trigger coordinator refresh to update all entities with new data
            await self.coordinator.async_request_refresh()

            _LOGGER.info("Successfully refreshed hardware info for dial %s", self._dial_uid)

        except Exception as err:
            _LOGGER.error("Failed to refresh hardware info for dial %s: %s", self._dial_uid, err)
            raise


class VU1IdentifyDialButton(VU1DialEntity, CoordinatorEntity, ButtonEntity):
    """Button to identify a VU1 dial with white flash animation."""

    def __init__(self, coordinator, dial_uid: str) -> None:
        """Initialize the identify button."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_identify"
        self._attr_translation_key = "identify"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press - perform identify animation in background."""
        # Get current backlight state to restore later
        original_backlight = None
        if self.coordinator.data:
            dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid, {})
            detailed_status = dial_data.get("detailed_status", {})
            original_backlight = detailed_status.get("backlight", {})

        async def _run_identify() -> None:
            try:
                _LOGGER.info("Starting identify animation for dial %s", self._dial_uid)

                # Flash sequence: white for 1s, off for 1s, then restore
                await self.coordinator.client.set_dial_backlight(self._dial_uid, 100, 100, 100)
                await asyncio.sleep(1.0)
                await self.coordinator.client.set_dial_backlight(self._dial_uid, 0, 0, 0)
                await asyncio.sleep(1.0)

                # Restore original state
                if original_backlight:
                    red = original_backlight.get("red", 0)
                    green = original_backlight.get("green", 0)
                    blue = original_backlight.get("blue", 0)
                    await self.coordinator.client.set_dial_backlight(self._dial_uid, red, green, blue)
                else:
                    red, green, blue = 0, 0, 0
                    await self.coordinator.client.set_dial_backlight(self._dial_uid, red, green, blue)

                # Optimistically write the restored color into coordinator data
                # instead of polling: the server applies queued commands ~1s
                # later, so an immediate refresh would read pre-restore state.
                self._optimistically_restore_backlight(red, green, blue)
                _LOGGER.info("Completed identify animation for dial %s", self._dial_uid)

            except Exception as err:
                _LOGGER.error("Failed to perform identify animation for dial %s: %s", self._dial_uid, err)
                if original_backlight:
                    try:
                        red = original_backlight.get("red", 0)
                        green = original_backlight.get("green", 0)
                        blue = original_backlight.get("blue", 0)
                        await self.coordinator.client.set_dial_backlight(self._dial_uid, red, green, blue)
                        self._optimistically_restore_backlight(red, green, blue)
                    except Exception:
                        _LOGGER.error("Failed to restore original backlight state for dial %s", self._dial_uid)

        self.coordinator.config_entry.async_create_background_task(
            self.hass, _run_identify(), name=f"vu1_identify_{self._dial_uid}"
        )

    def _optimistically_restore_backlight(self, red: int, green: int, blue: int) -> None:
        """Write the restored backlight into coordinator data and notify entities."""
        if not self.coordinator.data:
            return
        dial_data = self.coordinator.data.get("dials", {}).get(self._dial_uid)
        if dial_data is None:
            return
        dial_data.setdefault("detailed_status", {})["backlight"] = {
            "red": red,
            "green": green,
            "blue": blue,
        }
        # Push the optimistic state to all coordinator-bound entities (the
        # backlight light reads from coordinator data), without re-polling.
        self.coordinator.async_update_listeners()
