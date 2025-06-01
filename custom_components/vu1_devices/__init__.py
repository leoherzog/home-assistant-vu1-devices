"""The VU1 Devices integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_HOST,
    CONF_PORT,
    CONF_API_KEY,
    DEFAULT_UPDATE_INTERVAL,
    SERVICE_SET_DIAL_VALUE,
    SERVICE_SET_DIAL_BACKLIGHT,
    SERVICE_SET_DIAL_NAME,
    SERVICE_RELOAD_DIAL,
    SERVICE_CALIBRATE_DIAL,
    ATTR_DIAL_UID,
    ATTR_VALUE,
    ATTR_RED,
    ATTR_GREEN,
    ATTR_BLUE,
    ATTR_NAME,
)
from .vu1_api import VU1APIClient, VU1APIError

_LOGGER = logging.getLogger(__name__)


class VU1DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching VU1 data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: VU1APIClient,
        update_interval: timedelta,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from VU1 server."""
        try:
            dials = await self.client.get_dial_list()
            
            # Get detailed status for each dial
            dial_data = {}
            for dial in dials:
                dial_uid = dial["uid"]
                try:
                    status = await self.client.get_dial_status(dial_uid)
                    dial_data[dial_uid] = {**dial, "detailed_status": status}
                except VU1APIError as err:
                    _LOGGER.warning("Failed to get status for dial %s: %s", dial_uid, err)
                    dial_data[dial_uid] = dial

            return dial_data

        except VU1APIError as err:
            raise UpdateFailed(f"Error communicating with VU1 server: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up VU1 Devices from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    api_key = entry.data[CONF_API_KEY]

    client = VU1APIClient(host, port, api_key)

    # Test connection
    try:
        if not await client.test_connection():
            raise ConfigEntryNotReady("Cannot connect to VU1 server")
    except VU1APIError as err:
        raise ConfigEntryNotReady(f"Failed to connect to VU1 server: {err}") from err

    # Create coordinator
    update_interval = timedelta(
        seconds=entry.options.get("update_interval", DEFAULT_UPDATE_INTERVAL)
    )

    coordinator = VU1DataUpdateCoordinator(hass, client, update_interval)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Register the VU1 server as a device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"vu1_server_{host}_{port}")},
        manufacturer="Streacom",
        model="VU1 Server",
        name=f"VU1 Server ({host}:{port})",
        sw_version="1.0",
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await async_setup_services(hass, client)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["client"].close()

    # Remove services if this is the last entry
    if not hass.data[DOMAIN]:
        for service in [
            SERVICE_SET_DIAL_VALUE,
            SERVICE_SET_DIAL_BACKLIGHT,
            SERVICE_SET_DIAL_NAME,
            SERVICE_RELOAD_DIAL,
            SERVICE_CALIBRATE_DIAL,
        ]:
            hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def async_setup_services(hass: HomeAssistant, client: VU1APIClient) -> None:
    """Set up services for VU1 integration."""

    async def set_dial_value(call: ServiceCall) -> None:
        """Set dial value service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        value = call.data[ATTR_VALUE]
        
        # Find the correct client for this dial
        client = None
        coordinator = None
        for entry_id, data in hass.data[DOMAIN].items():
            coord = data["coordinator"]
            if coord.data and dial_uid in coord.data:
                client = data["client"]
                coordinator = coord
                break
        
        if not client:
            _LOGGER.error("Dial %s not found", dial_uid)
            return
        
        try:
            await client.set_dial_value(dial_uid, value)
            # Refresh the coordinator that owns this dial
            await coordinator.async_request_refresh()
            _LOGGER.debug("Successfully set dial %s value to %s", dial_uid, value)
        except VU1APIError as err:
            _LOGGER.error("Failed to set dial value for %s: %s", dial_uid, err)

    async def set_dial_backlight(call: ServiceCall) -> None:
        """Set dial backlight service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        red = call.data[ATTR_RED]
        green = call.data[ATTR_GREEN]
        blue = call.data[ATTR_BLUE]
        
        # Find the correct client for this dial
        client = None
        coordinator = None
        for entry_id, data in hass.data[DOMAIN].items():
            coord = data["coordinator"]
            if coord.data and dial_uid in coord.data:
                client = data["client"]
                coordinator = coord
                break
        
        if not client:
            _LOGGER.error("Dial %s not found", dial_uid)
            return
        
        try:
            await client.set_dial_backlight(dial_uid, red, green, blue)
            await coordinator.async_request_refresh()
        except VU1APIError as err:
            _LOGGER.error("Failed to set dial backlight for %s: %s", dial_uid, err)

    async def set_dial_name(call: ServiceCall) -> None:
        """Set dial name service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        name = call.data[ATTR_NAME]
        
        # Find the correct client for this dial
        client = None
        coordinator = None
        for entry_id, data in hass.data[DOMAIN].items():
            coord = data["coordinator"]
            if coord.data and dial_uid in coord.data:
                client = data["client"]
                coordinator = coord
                break
        
        if not client:
            _LOGGER.error("Dial %s not found", dial_uid)
            return
        
        try:
            await client.set_dial_name(dial_uid, name)
            await coordinator.async_request_refresh()
        except VU1APIError as err:
            _LOGGER.error("Failed to set dial name for %s: %s", dial_uid, err)

    async def reload_dial(call: ServiceCall) -> None:
        """Reload dial service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        
        # Find the correct client for this dial
        client = None
        coordinator = None
        for entry_id, data in hass.data[DOMAIN].items():
            coord = data["coordinator"]
            if coord.data and dial_uid in coord.data:
                client = data["client"]
                coordinator = coord
                break
        
        if not client:
            _LOGGER.error("Dial %s not found", dial_uid)
            return
        
        try:
            await client.reload_dial(dial_uid)
            await coordinator.async_request_refresh()
        except VU1APIError as err:
            _LOGGER.error("Failed to reload dial for %s: %s", dial_uid, err)

    async def calibrate_dial(call: ServiceCall) -> None:
        """Calibrate dial service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        
        # Find the correct client for this dial
        client = None
        coordinator = None
        for entry_id, data in hass.data[DOMAIN].items():
            coord = data["coordinator"]
            if coord.data and dial_uid in coord.data:
                client = data["client"]
                coordinator = coord
                break
        
        if not client:
            _LOGGER.error("Dial %s not found", dial_uid)
            return
        
        try:
            await client.calibrate_dial(dial_uid)
            await coordinator.async_request_refresh()
        except VU1APIError as err:
            _LOGGER.error("Failed to calibrate dial for %s: %s", dial_uid, err)

    # Register services
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DIAL_VALUE,
        set_dial_value,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DIAL_UID): cv.string,
                vol.Required(ATTR_VALUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DIAL_BACKLIGHT,
        set_dial_backlight,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DIAL_UID): cv.string,
                vol.Required(ATTR_RED): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Required(ATTR_GREEN): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Required(ATTR_BLUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DIAL_NAME,
        set_dial_name,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DIAL_UID): cv.string,
                vol.Required(ATTR_NAME): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD_DIAL,
        reload_dial,
        schema=vol.Schema({vol.Required(ATTR_DIAL_UID): cv.string}),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE_DIAL,
        calibrate_dial,
        schema=vol.Schema({vol.Required(ATTR_DIAL_UID): cv.string}),
    )