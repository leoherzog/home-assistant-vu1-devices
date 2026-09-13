"""Base entity for the VU1 Dials integration."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BACKLIGHT_CHANNELS, CONF_BACKLIGHT_COLOR, DOMAIN, MANUFACTURER, MODEL
from .coordinator import VU1DataUpdateCoordinator
from .device_config import async_get_config_manager

if TYPE_CHECKING:
    from . import VU1ConfigEntry


def get_dial_device_info(coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> DeviceInfo:
    """Return device info for a VU1 dial."""
    dial = coordinator.data["dials"].get(dial_uid, {})
    status = dial.get("detailed_status") or {}
    return DeviceInfo(
        identifiers={(DOMAIN, dial_uid)},
        name=dial.get("dial_name", f"VU1 Dial {dial_uid}"),
        manufacturer=MANUFACTURER,
        model=MODEL,
        sw_version=status.get("fw_version"),
        hw_version=status.get("hw_version"),
        serial_number=dial_uid,
        via_device_id=coordinator.hub_device_id,
    )


class VU1DialEntity(CoordinatorEntity[VU1DataUpdateCoordinator]):
    """Base class for entities that belong to a single VU1 dial."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the dial entity."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_device_info = get_dial_device_info(coordinator, dial_uid)

    @property
    def dial_data(self) -> dict[str, Any]:
        """Return this dial's entry in the latest coordinator data."""
        return self.coordinator.data["dials"].get(self._dial_uid, {})

    @property
    def available(self) -> bool:
        """Return True if the dial is present in the latest coordinator data."""
        return super().available and self._dial_uid in self.coordinator.data["dials"]

    def _backlight(self) -> list[int]:
        """Return the current RGBW backlight in the device's 0-100 range."""
        dial = self.dial_data
        backlight = dial.get("detailed_status", {}).get("backlight") or dial.get("backlight") or {}
        # The server strips white from /dial/list (and with it from /status), so
        # a missing channel falls back to the last colour HA set.
        stored = async_get_config_manager(self.hass).get_dial_config(self._dial_uid)[CONF_BACKLIGHT_COLOR]
        return [backlight.get(channel, value) for channel, value in zip(BACKLIGHT_CHANNELS, stored)]


@callback
def async_setup_dial_entities(
    entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_factory: Callable[[str], list[Entity]],
) -> None:
    """Add entities for current dials and for dials that appear on later refreshes."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _async_add_new_dials() -> None:
        new_uids = [uid for uid in coordinator.data["dials"] if uid not in known]
        if new_uids:
            known.update(new_uids)
            async_add_entities([entity for uid in new_uids for entity in entity_factory(uid)])

    _async_add_new_dials()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_dials))
