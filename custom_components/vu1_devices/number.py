"""Support for VU1 dial number entities."""
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .vu1_api import VU1APIClient

if TYPE_CHECKING:
    from . import VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 number entities."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    client: VU1APIClient = data["client"]

    entities = []
    
    # Create number entities for each dial
    for dial_uid, dial_data in coordinator.data.items():
        entities.append(VU1DialNumber(coordinator, client, dial_uid, dial_data))

    async_add_entities(entities)


class VU1DialNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Representation of a VU1 dial number entity."""

    def __init__(
        self,
        coordinator,
        client: VU1APIClient,
        dial_uid: str,
        dial_data: Dict[str, Any],
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._client = client
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{DOMAIN}_dial_{dial_uid}"
        self._attr_name = f"{dial_data.get('dial_name', f'VU1 Dial {dial_uid}')} Value"
        self._attr_has_entity_name = True
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = NumberMode.SLIDER
        self._attr_icon = "mdi:gauge"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this VU1 dial."""
        dial_data = self.coordinator.data.get(self._dial_uid, {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._dial_uid)},
            name=dial_data.get("dial_name", f"VU1 Dial {self._dial_uid}"),
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version="1.0",
            # Add via_device to link to the VU1 server hub
            via_device=(DOMAIN, f"vu1_server_{self._client.host}_{self._client.port}"),
        )

    @property
    def native_value(self) -> Optional[float]:
        """Return the current value."""
        dial_data = self.coordinator.data.get(self._dial_uid, {})
        return dial_data.get("value")

    async def async_set_native_value(self, value: float) -> None:
        """Set the dial value."""
        try:
            await self._client.set_dial_value(self._dial_uid, int(value))
            # Trigger coordinator refresh to update state
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set dial value for %s: %s", self._dial_uid, err)
            raise

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        dial_data = self.coordinator.data.get(self._dial_uid, {})
        
        attributes = {
            "dial_uid": self._dial_uid,
            "dial_name": dial_data.get("dial_name"),
        }

        # Add backlight information
        backlight = dial_data.get("backlight", {})
        if backlight:
            attributes.update({
                "backlight_red": backlight.get("red"),
                "backlight_green": backlight.get("green"),
                "backlight_blue": backlight.get("blue"),
            })

        return attributes

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Restore previous state if available
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state != "unknown":
            self._attr_native_value = float(last_state.state)