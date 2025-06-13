"""The VU1 Dials integration."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
    DEFAULT_TIMEOUT,
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
        self._previous_dial_names: Dict[str, str] = {}
        self._name_change_grace_periods: Dict[str, datetime] = {}
        self._behavior_change_grace_periods: Dict[str, datetime] = {}
        self._grace_period_seconds = 10
        self.server_device_id: Optional[str] = None

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from VU1 server."""
        try:
            dials = await self.client.get_dial_list()
            
            if not isinstance(dials, list):
                _LOGGER.error("Unexpected dial list format: %s", type(dials))
                raise UpdateFailed("Invalid dial list format")
            
            # Get detailed status for each dial
            dial_data = {}
            for dial in dials:
                if not isinstance(dial, dict) or "uid" not in dial:
                    _LOGGER.warning("Invalid dial data: %s", dial)
                    continue
                    
                dial_uid = dial["uid"]
                try:
                    status = await self.client.get_dial_status(dial_uid)
                    dial_data[dial_uid] = {**dial, "detailed_status": status}
                    
                    # Check for server-side name changes
                    server_name = dial.get("dial_name")
                    if server_name is not None:
                        await self._check_server_name_change(dial_uid, server_name)
                    
                    # Check for server-side behavior preset changes
                    await self._check_server_behavior_change(dial_uid, status)
                    
                except VU1APIError as err:
                    _LOGGER.warning("Failed to get status for dial %s: %s", dial_uid, err)
                    # Still include the dial with basic info
                    dial_data[dial_uid] = {**dial, "detailed_status": {}}

            # Update sensor bindings when data changes
            if hasattr(self, '_binding_manager') and self._binding_manager:
                await self._binding_manager.async_update_bindings({"dials": dial_data})

            # Return data structure with dials
            return {"dials": dial_data}

        except VU1APIError as err:
            _LOGGER.error("VU1 API error: %s", err)
            raise UpdateFailed(f"Error communicating with VU1 server: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error updating VU1 data")
            raise UpdateFailed(f"Unexpected error: {err}") from err
    
    def set_binding_manager(self, binding_manager) -> None:
        """Set the binding manager reference."""
        self._binding_manager = binding_manager
    
    async def _check_server_name_change(self, dial_uid: str, server_name: str) -> None:
        """Check if the server name has changed and sync to HA if needed."""
        if not server_name:
            return
            
        previous_name = self._previous_dial_names.get(dial_uid)
        current_time = datetime.now()
        
        # Check if we're in a grace period (recently changed name from HA side)
        grace_end = self._name_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug(
                "Ignoring server name change for %s during grace period", dial_uid
            )
            return
            
        # If name changed on server, update HA
        if previous_name and previous_name != server_name:
            _LOGGER.info(
                "Server name changed for %s: %s -> %s", 
                dial_uid, previous_name, server_name
            )
            await self._update_ha_name(dial_uid, server_name)
            
        # Store current name for next comparison
        self._previous_dial_names[dial_uid] = server_name
        
    async def _update_ha_name(self, dial_uid: str, new_name: str) -> None:
        """Update entity and device names in Home Assistant."""
        try:
            # Update entity name
            entity_registry = er.async_get(self.hass)
            entity_id = entity_registry.async_get_entity_id(
                "sensor", DOMAIN, f"{DOMAIN}_{dial_uid}"
            )
            
            if entity_id:
                entity_entry = entity_registry.async_get(entity_id)
                # Only update if not user-customized
                if entity_entry and not entity_entry.name:
                    entity_registry.async_update_entity(
                        entity_id,
                        original_name=new_name
                    )
                    _LOGGER.debug(
                        "Updated entity original_name for %s to %s", 
                        entity_id, new_name
                    )
            
            # Update device name
            device_registry = dr.async_get(self.hass)
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, dial_uid)}
            )
            
            if device and not device.name_by_user:
                device_registry.async_update_device(
                    device.id,
                    name=new_name
                )
                _LOGGER.info(
                    "Updated device name for %s to %s", dial_uid, new_name
                )
                
        except Exception as err:
            _LOGGER.error(
                "Failed to update HA name for %s: %s", dial_uid, err
            )
            
    def mark_name_change_from_ha(self, dial_uid: str) -> None:
        """Mark that a name change originated from HA to prevent sync loops."""
        grace_end = datetime.now() + timedelta(seconds=self._grace_period_seconds)
        self._name_change_grace_periods[dial_uid] = grace_end
        _LOGGER.debug(
            "Started grace period for %s until %s", 
            dial_uid, grace_end.isoformat()
        )

    def mark_behavior_change_from_ha(self, dial_uid: str) -> None:
        """Mark that a behavior change originated from HA to prevent sync loops."""
        grace_end = datetime.now() + timedelta(seconds=self._grace_period_seconds)
        self._behavior_change_grace_periods[dial_uid] = grace_end
        _LOGGER.debug(
            "Started behavior grace period for %s until %s",
            dial_uid, grace_end.isoformat()
        )
    
    async def _check_server_behavior_change(self, dial_uid: str, status: Dict[str, Any]) -> None:
        """Check if server behavior settings changed and sync to HA."""
        if not status:
            return
            
        current_time = datetime.now()
        grace_end = self._behavior_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring server behavior change for %s during grace period", dial_uid)
            return
            
        # Extract easing settings from server status
        easing_config = status.get("easing", {})
        if not easing_config:
            return
            
        # Get current HA configuration
        from .device_config import async_get_config_manager
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(dial_uid)
        
        # Check if server values differ from HA config
        # Convert string values to int if needed for consistent comparison
        dial_period = easing_config.get("dial_period", 50)
        if isinstance(dial_period, str):
            dial_period = int(dial_period)
        backlight_period = easing_config.get("backlight_period", 50)
        if isinstance(backlight_period, str):
            backlight_period = int(backlight_period)
        dial_step = easing_config.get("dial_step", 5)
        if isinstance(dial_step, str):
            dial_step = int(dial_step)
        backlight_step = easing_config.get("backlight_step", 5)
        if isinstance(backlight_step, str):
            backlight_step = int(backlight_step)
            
        server_values = {
            "dial_easing_period": dial_period,
            "dial_easing_step": dial_step,
            "backlight_easing_period": backlight_period,
            "backlight_easing_step": backlight_step,
        }
        
        config_changed = False
        for key, server_value in server_values.items():
            if current_config.get(key) != server_value:
                config_changed = True
                _LOGGER.info(
                    "Server %s changed for %s: %s -> %s",
                    key, dial_uid, current_config.get(key), server_value
                )
        
        if config_changed:
            # Update HA configuration to match server
            updated_config = {**current_config, **server_values}
            await config_manager.async_update_dial_config(dial_uid, updated_config)
            _LOGGER.info("Synced behavior settings from server for %s", dial_uid)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up VU1 Dials from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    
    # Get configurable timeout from options
    timeout = entry.options.get("timeout", DEFAULT_TIMEOUT)

    # Create client based on connection type
    if entry.data.get("ingress"):
        # Ingress connection via internal hostname
        host = entry.data[CONF_HOST]  # local-{slug}
        port = entry.data[CONF_PORT]
        client = VU1APIClient(
            host=host,
            port=port,
            ingress_slug=entry.data["ingress_slug"],
            supervisor_token=entry.data["supervisor_token"],
            api_key=api_key,
            timeout=timeout,
        )
        connection_info = f"ingress ({entry.data['ingress_slug']})"
        device_identifier = f"vu1_server_ingress_{entry.data['ingress_slug']}"
    else:
        # Direct connection
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        client = VU1APIClient(host, port, api_key, timeout=timeout)
        connection_info = f"{host}:{port}"
        device_identifier = f"vu1_server_{host}_{port}"

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
    coordinator.server_device_id = device_identifier

    # Set up device configuration manager
    from .device_config import async_get_config_manager
    config_manager = async_get_config_manager(hass)
    await config_manager.async_load()
    
    # Set up sensor binding manager BEFORE first refresh
    from .sensor_binding import async_get_binding_manager
    binding_manager = async_get_binding_manager(hass)
    await binding_manager.async_setup()
    
    # Connect binding manager to coordinator BEFORE first refresh
    coordinator.set_binding_manager(binding_manager)
    
    # Register the VU1 server as a device BEFORE setting up platforms
    device_registry = dr.async_get(hass)
    
    # Determine device name based on connection type
    if entry.data.get("ingress"):
        # Extract add-on name from slug
        addon_slug = entry.data.get("ingress_slug", "")
        addon_name = addon_slug.split("_")[-1] if "_" in addon_slug else addon_slug
        device_name = f"Add-on ({addon_name})"
    else:
        device_name = f"VU1 Server ({connection_info})"
    
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_identifier)},
        manufacturer="Streacom",
        model="VU1 Server",
        name=device_name,
        sw_version="1.0",
    )

    # NOW fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }
    
    # Add binding manager to data for coordinator access
    hass.data[DOMAIN][entry.entry_id]["binding_manager"] = binding_manager
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await async_setup_services(hass, client)
    
    # Initial binding update
    if coordinator.data:
        dials_data = coordinator.data.get("dials", {})
        await binding_manager.async_update_bindings(dials_data)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["client"].close()
        
        # Shutdown binding manager for this entry
        binding_manager = data.get("binding_manager")
        if binding_manager:
            await binding_manager.async_shutdown()
        
        # Remove server device from registry
        device_registry = dr.async_get(hass)
        if entry.data.get("ingress"):
            device_identifier = f"vu1_server_ingress_{entry.data['ingress_slug']}"
        else:
            device_identifier = f"vu1_server_{entry.data[CONF_HOST]}_{entry.data[CONF_PORT]}"
        
        server_device_id = (DOMAIN, device_identifier)
        device = device_registry.async_get_device(identifiers={server_device_id})
        if device:
            device_registry.async_remove_device(device.id)
        

    return unload_ok


def _get_dial_client_and_coordinator(hass: HomeAssistant, dial_uid: str) -> tuple[VU1APIClient, VU1DataUpdateCoordinator] | None:
    """Find the correct client and coordinator for a dial."""
    for data in hass.data[DOMAIN].values():
        coord = data["coordinator"]
        if coord.data and dial_uid in coord.data.get("dials", {}):
            return data["client"], coord
    return None


async def _execute_dial_service(
    hass: HomeAssistant, 
    dial_uid: str, 
    action_name: str, 
    api_call,
    refresh: bool = True
) -> None:
    """Execute a dial service with common error handling."""
    # Validate dial_uid is not empty
    if not dial_uid or not isinstance(dial_uid, str):
        _LOGGER.error("Invalid dial_uid provided: %s", dial_uid)
        raise ValueError(f"Invalid dial_uid: {dial_uid}")
    
    result = _get_dial_client_and_coordinator(hass, dial_uid)
    if not result:
        _LOGGER.error("Dial %s not found", dial_uid)
        raise ValueError(f"Dial {dial_uid} not found")
    
    client, coordinator = result
    try:
        await api_call(client)
        if refresh:
            await coordinator.async_request_refresh()
    except VU1APIError as err:
        _LOGGER.error("Failed to %s for %s: %s", action_name, dial_uid, err)
        raise
    except Exception as err:
        _LOGGER.error("Unexpected error during %s for %s: %s", action_name, dial_uid, err)
        raise


async def async_setup_services(hass: HomeAssistant, client: VU1APIClient) -> None:
    """Set up services for VU1 integration."""

    async def set_dial_value(call: ServiceCall) -> None:
        """Set dial value service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        value = call.data[ATTR_VALUE]
        
        await _execute_dial_service(
            hass, dial_uid, "set dial value",
            lambda client: client.set_dial_value(dial_uid, value)
        )

    async def set_dial_backlight(call: ServiceCall) -> None:
        """Set dial backlight service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        red = call.data[ATTR_RED]
        green = call.data[ATTR_GREEN]
        blue = call.data[ATTR_BLUE]
        
        await _execute_dial_service(
            hass, dial_uid, "set dial backlight",
            lambda client: client.set_dial_backlight(dial_uid, red, green, blue)
        )

    async def set_dial_name(call: ServiceCall) -> None:
        """Set dial name service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        name = call.data[ATTR_NAME]
        
        # Mark grace period to prevent sync loop
        result = _get_dial_client_and_coordinator(hass, dial_uid)
        if result:
            _, coordinator = result
            coordinator.mark_name_change_from_ha(dial_uid)
        
        await _execute_dial_service(
            hass, dial_uid, "set dial name",
            lambda client: client.set_dial_name(dial_uid, name)
        )

    async def reload_dial(call: ServiceCall) -> None:
        """Reload dial service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        
        await _execute_dial_service(
            hass, dial_uid, "reload dial",
            lambda client: client.reload_dial(dial_uid)
        )

    async def calibrate_dial(call: ServiceCall) -> None:
        """Calibrate dial service."""
        dial_uid = call.data[ATTR_DIAL_UID]
        
        await _execute_dial_service(
            hass, dial_uid, "calibrate dial",
            lambda client: client.calibrate_dial(dial_uid)
        )

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