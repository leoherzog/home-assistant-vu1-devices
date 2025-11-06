"""The VU1 Dials integration."""
import asyncio
import logging
import mimetypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback, Event
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED, EventDeviceRegistryUpdatedData
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util
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
    SERVICE_SET_DIAL_IMAGE,
    SERVICE_RELOAD_DIAL,
    SERVICE_CALIBRATE_DIAL,
    ATTR_DIAL_UID,
    ATTR_VALUE,
    ATTR_RED,
    ATTR_GREEN,
    ATTR_BLUE,
    ATTR_NAME,
    ATTR_MEDIA_CONTENT_ID,
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
        # Track last known names to detect server-side changes
        self._previous_dial_names: Dict[str, str] = {}
        # Prevent sync loops when name changes originate from HA
        self._name_change_grace_periods: Dict[str, datetime] = {}
        self._behavior_change_grace_periods: Dict[str, datetime] = {}
        self._grace_period_seconds = 10
        # Store device identifier string for via_device relationships, not internal device.id
        self.server_device_identifier: Optional[str] = None

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from VU1 server."""
        try:
            dials = await self.client.get_dial_list()
            
            if not isinstance(dials, list):
                _LOGGER.error("Unexpected dial list format: %s", type(dials))
                raise UpdateFailed("Invalid dial list format")
            
            # Get detailed status for each dial
            dial_data: Dict[str, Any] = {}
            dial_refs: List[tuple[str, Dict[str, Any]]] = []
            dial_tasks: List[Awaitable[Dict[str, Any]]] = []

            for dial in dials:
                if not isinstance(dial, dict) or "uid" not in dial:
                    _LOGGER.warning("Invalid dial data: %s", dial)
                    continue

                dial_uid = dial["uid"]
                dial_refs.append((dial_uid, dial))
                dial_tasks.append(self.client.get_dial_status(dial_uid))

            if dial_tasks:
                results = await asyncio.gather(*dial_tasks, return_exceptions=True)
            else:
                results = []

            for (dial_uid, dial), result in zip(dial_refs, results):
                if isinstance(result, BaseException):
                    if isinstance(result, VU1APIError):
                        _LOGGER.warning("Failed to get status for dial %s: %s", dial_uid, result)
                    elif isinstance(result, asyncio.CancelledError):
                        _LOGGER.debug("Status update cancelled for dial %s", dial_uid)
                    else:
                        _LOGGER.error("Unexpected error getting status for dial %s", dial_uid, exc_info=result)
                    dial_data[dial_uid] = {**dial, "detailed_status": {}}
                    continue

                status: Dict[str, Any] = result
                dial_data[dial_uid] = {**dial, "detailed_status": status}

                await self._sync_name_from_server(dial_uid, dial.get("dial_name"))
                await self._check_server_behavior_change(dial_uid, status)

            if hasattr(self, '_binding_manager') and self._binding_manager:
                await self._binding_manager.async_update_bindings({"dials": dial_data})

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
    
    async def _sync_name_from_server(self, dial_uid: str, server_name: Optional[str]) -> None:
        """Sync device name from server to Home Assistant if it has changed."""
        if not server_name:
            return

        # Check if we're in a grace period (change originated from HA)
        current_time = dt_util.utcnow()
        grace_end = self._name_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring server name change for %s during grace period", dial_uid)
            return

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, dial_uid)})

        if device and not device.name_by_user and device.name != server_name:
            _LOGGER.info("Server name for %s changed ('%s' -> '%s'). Updating device.", dial_uid, device.name, server_name)
            device_registry.async_update_device(device.id, name=server_name)

        self._previous_dial_names[dial_uid] = server_name
            
    def mark_name_change_from_ha(self, dial_uid: str) -> None:
        """Mark that a name change originated from HA to prevent sync loops."""
        grace_end = dt_util.utcnow() + timedelta(seconds=self._grace_period_seconds)
        self._name_change_grace_periods[dial_uid] = grace_end
        _LOGGER.debug("Started name change grace period for %s until %s", dial_uid, grace_end.isoformat())

    async def async_set_dial_name(self, dial_uid: str, new_name: str) -> None:
        """Set the dial name on the server and update HA. Centralized method."""
        # Mark that this change originated from HA to prevent sync loops
        self.mark_name_change_from_ha(dial_uid)
        
        try:
            # 1. Update the VU1 Server
            await self.client.set_dial_name(dial_uid, new_name)
            # 2. Update our internal tracker
            self._previous_dial_names[dial_uid] = new_name
            
            # 3. Update the HA device registry
            device_registry = dr.async_get(self.hass)
            device = device_registry.async_get_device(identifiers={(DOMAIN, dial_uid)})
            if device:
                device_registry.async_update_device(device.id, name=new_name)
            
            _LOGGER.info("Successfully synced name '%s' to server for dial %s", new_name, dial_uid)
            # 4. Refresh coordinator to ensure consistency
            await self.async_request_refresh()

        except VU1APIError as err:
            _LOGGER.error("Failed to set dial name for %s on server: %s", dial_uid, err)
            # Clear grace period on failure to allow future updates
            self._name_change_grace_periods.pop(dial_uid, None)
            raise

    async def _handle_device_name_change(self, dial_uid: str, new_name: str) -> None:
        """Handle device name change from HA UI."""
        # Check if we're in a grace period (change originated from server)
        current_time = dt_util.utcnow()
        grace_end = self._name_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring HA name change for %s during grace period", dial_uid)
            return
            
        # Check if name actually changed
        if self._previous_dial_names.get(dial_uid) == new_name:
            return
            
        _LOGGER.info("Device name changed in HA for dial %s: '%s'", dial_uid, new_name)
        
        # Sync to server using existing method
        try:
            await self.async_set_dial_name(dial_uid, new_name)
        except Exception as err:
            _LOGGER.error("Failed to sync device name to server: %s", err)

    def mark_behavior_change_from_ha(self, dial_uid: str) -> None:
        """Mark that a behavior change originated from HA to prevent sync loops."""
        grace_end = dt_util.utcnow() + timedelta(seconds=self._grace_period_seconds)
        self._behavior_change_grace_periods[dial_uid] = grace_end
        _LOGGER.debug(
            "Started behavior grace period for %s until %s",
            dial_uid, grace_end.isoformat()
        )
    
    async def _check_server_behavior_change(self, dial_uid: str, status: Dict[str, Any]) -> None:
        """Check if server behavior settings changed and sync to HA."""
        if not status:
            return
            
        current_time = dt_util.utcnow()
        grace_end = self._behavior_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring server behavior change for %s during grace period", dial_uid)
            return
            
        easing_config = status.get("easing", {})
        if not easing_config:
            return
            
        from .device_config import async_get_config_manager
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(dial_uid)
        # Convert server values to int with fallbacks for invalid data
        try:
            dial_period = int(easing_config.get("dial_period", 50))
        except (ValueError, TypeError):
            dial_period = 50
            
        try:
            backlight_period = int(easing_config.get("backlight_period", 50))
        except (ValueError, TypeError):
            backlight_period = 50
            
        try:
            dial_step = int(easing_config.get("dial_step", 5))
        except (ValueError, TypeError):
            dial_step = 5
            
        try:
            backlight_step = int(easing_config.get("backlight_step", 5))
        except (ValueError, TypeError):
            backlight_step = 5
            
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
            # Update HA config to match server values
            updated_config = {**current_config, **server_values}
            await config_manager.async_update_dial_config(dial_uid, updated_config)
            _LOGGER.info("Synced behavior settings from server for %s", dial_uid)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up VU1 Dials from a config entry."""
    api_key = entry.data[CONF_API_KEY]
    timeout = entry.options.get("timeout", DEFAULT_TIMEOUT)
    
    # Create client based on connection type (ingress vs direct)
    if entry.data.get("ingress"):
        host = entry.data[CONF_HOST]
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
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        client = VU1APIClient(host, port, api_key, timeout=timeout)
        connection_info = f"{host}:{port}"
        device_identifier = f"vu1_server_{host}_{port}"

    try:
        connection_result = await client.test_connection()
        if not connection_result["connected"]:
            error_msg = connection_result["error"] or "Unknown connection error"
            raise ConfigEntryNotReady(f"Cannot connect to VU1 server: {error_msg}")
        
        # Log authentication status for debugging
        if connection_result["authenticated"]:
            _LOGGER.debug("VU1 server connection successful with valid API key")
        else:
            _LOGGER.warning("VU1 server reachable but API key validation failed: %s", 
                          connection_result["error"])
    except VU1APIError as err:
        raise ConfigEntryNotReady(f"Failed to connect to VU1 server: {err}") from err

    update_interval = timedelta(
        seconds=entry.options.get("update_interval", DEFAULT_UPDATE_INTERVAL)
    )

    coordinator = VU1DataUpdateCoordinator(hass, client, update_interval)
    # Store the device identifier string for proper via_device relationships
    coordinator.server_device_identifier = device_identifier

    # Set up device configuration manager
    from .device_config import async_get_config_manager
    config_manager = async_get_config_manager(hass)
    await config_manager.async_load()
    
    # Set up sensor binding manager before first data refresh
    from .sensor_binding import async_get_binding_manager
    binding_manager = async_get_binding_manager(hass)
    await binding_manager.async_setup()
    
    # Connect binding manager to coordinator
    coordinator.set_binding_manager(binding_manager)
    
    # Register the VU1 server as a hub device
    device_registry = dr.async_get(hass)
    
    # Determine device name based on connection type
    if entry.data.get("ingress"):
        # Extract add-on name from slug for display
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

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "binding_manager": binding_manager,
    }
    
    # Set up device registry listener for bidirectional name sync
    @callback
    def handle_device_registry_updated(event: Event[EventDeviceRegistryUpdatedData]) -> None:
        """Handle device registry updates."""
        device_id = event.data["device_id"]
        changes = event.data["changes"]
        
        if "name_by_user" not in changes:
            return
            
        # Check if this is a VU1 dial device
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        
        if not device:
            return
            
        # Check if it's one of our dial devices
        for identifier_domain, identifier_value in device.identifiers:
            if identifier_domain == DOMAIN and not identifier_value.startswith("vu1_server_"):
                # This is a dial device
                dial_uid = identifier_value
                new_name = device.name_by_user or device.name
                hass.async_create_task(
                    coordinator._handle_device_name_change(dial_uid, new_name)
                )
                break
    
    # Register the device registry listener and bind its lifecycle to config entry
    entry.async_on_unload(
        hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, handle_device_registry_updated)
    )
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass, client)
    
    if coordinator.data:
        dials_data = coordinator.data.get("dials", {})
        await binding_manager.async_update_bindings({"dials": dials_data})

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        client = data["client"]
        coordinator = data["coordinator"]
        binding_manager = data.get("binding_manager")
        
        await client.close()
        
        if binding_manager:
            await binding_manager.async_shutdown()
        
        # Clean up server device from device registry
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, coordinator.server_device_identifier)}
        )
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
        
        result = _get_dial_client_and_coordinator(hass, dial_uid)
        if not result:
            _LOGGER.error("Dial %s not found for service call", dial_uid)
            raise ValueError(f"Dial {dial_uid} not found")
        
        _client, coordinator = result
        
        try:
            await coordinator.async_set_dial_name(dial_uid, name)
        except Exception as err:
            _LOGGER.error("Service call failed to set dial name for %s: %s", dial_uid, err)
            raise

    async def set_dial_image(call: ServiceCall) -> None:
        """Set dial background image service."""
        from homeassistant.components.media_source import async_resolve_media

        dial_uid = call.data[ATTR_DIAL_UID]
        media_content_id = call.data.get(ATTR_MEDIA_CONTENT_ID)
        
        if not media_content_id:
            _LOGGER.error("No media content ID provided for dial image")
            raise ValueError("Media content ID is required")
        
        try:
            # Resolve the media source URI to get actual file path/data
            _LOGGER.debug("Resolving media content ID: %s", media_content_id)
            resolved_media = await async_resolve_media(hass, media_content_id, None)
            
            if not resolved_media.url:
                raise ValueError("Could not resolve media content to URL")
            
            # Read the image data from the resolved URL
            if resolved_media.url.startswith("file://"):
                # Local file access
                file_path = resolved_media.url[7:]  # Remove 'file://' prefix
                
                # Use async-friendly file operations to avoid blocking the event loop
                if not await hass.async_add_executor_job(Path(file_path).exists):
                    raise ValueError(f"Media file not found: {file_path}")
                
                image_data = await hass.async_add_executor_job(Path(file_path).read_bytes)
                
                # Determine content type from file extension
                content_type, _ = mimetypes.guess_type(file_path)
                if not content_type or not content_type.startswith('image/'):
                    content_type = 'image/png'  # Default fallback
                    
            else:
                # Handle other URL types (HTTP, etc.) if needed
                session = async_get_clientsession(hass)
                async with session.get(resolved_media.url) as response:
                    if response.status != 200:
                        raise ValueError(f"Failed to fetch media: HTTP {response.status}")
                    image_data = await response.read()
                    content_type = response.headers.get('content-type', 'image/png')
            
            if not image_data:
                raise ValueError("No image data retrieved from media source")
            
            _LOGGER.debug("Retrieved image data: %d bytes, content-type: %s", len(image_data), content_type)
            
            # Upload to VU1 dial
            await _execute_dial_service(
                hass, dial_uid, "set dial image",
                lambda client: client.set_dial_image(dial_uid, image_data, content_type)
            )
            
            _LOGGER.info("Successfully set background image for dial %s", dial_uid)
            
        except Exception as err:
            _LOGGER.error("Failed to set dial image for %s: %s", dial_uid, err)
            raise

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
        SERVICE_SET_DIAL_IMAGE,
        set_dial_image,
        schema=vol.Schema(
            {
                vol.Required(ATTR_DIAL_UID): cv.string,
                vol.Optional(ATTR_MEDIA_CONTENT_ID): cv.string,
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
