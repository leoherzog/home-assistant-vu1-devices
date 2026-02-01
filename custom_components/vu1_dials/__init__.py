"""The VU1 Dials integration."""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback, Event
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED, EventDeviceRegistryUpdatedData
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

if TYPE_CHECKING:
    from .sensor_binding import VU1SensorBindingManager

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
    ATTR_VALUE,
    ATTR_RED,
    ATTR_GREEN,
    ATTR_BLUE,
    ATTR_NAME,
    ATTR_MEDIA_CONTENT_ID,
)
from .coordinator import VU1DataUpdateCoordinator
from .vu1_api import VU1APIClient, VU1APIError

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
    "VU1DataUpdateCoordinator",
    "VU1RuntimeData",
    "VU1ConfigEntry",
]


@dataclass
class VU1RuntimeData:
    """Runtime data for VU1 Dials integration."""

    client: VU1APIClient
    coordinator: VU1DataUpdateCoordinator
    binding_manager: VU1SensorBindingManager


type VU1ConfigEntry = ConfigEntry[VU1RuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the VU1 Dials integration."""
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
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

    # Connection validation is handled by coordinator._async_setup during first refresh
    # which automatically raises ConfigEntryNotReady on failure

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

    # Initialize known dial UIDs before platform setup to avoid race conditions
    if coordinator.data:
        initial_dial_uids = set(coordinator.data.get("dials", {}).keys())
        coordinator.update_known_dials(initial_dial_uids)

    # Store runtime data on the config entry (modern HA 2024.5+ pattern)
    entry.runtime_data = VU1RuntimeData(
        client=client,
        coordinator=coordinator,
        binding_manager=binding_manager,
    )

    # Set up device registry listener for bidirectional name sync
    @callback
    def handle_device_registry_updated(event: Event[EventDeviceRegistryUpdatedData]) -> None:
        """Handle device registry updates."""
        # Only process update events (not create/remove which don't have changes)
        if event.data.get("action") != "update":
            return

        device_id = event.data["device_id"]
        changes = event.data.get("changes", {})

        if "name_by_user" not in changes:
            return
            
        # Check if this is a VU1 dial device
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        
        if not device:
            return

        # Only handle updates for devices tied to this config entry
        if entry.entry_id not in device.config_entries:
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

    if coordinator.data:
        dials_data = coordinator.data.get("dials", {})
        await binding_manager.async_update_bindings({"dials": dials_data})

    return True


async def async_unload_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        runtime_data = entry.runtime_data

        await runtime_data.client.close()

        if runtime_data.binding_manager:
            await runtime_data.binding_manager.async_shutdown()

        # Clean up server device from device registry
        device_registry = dr.async_get(hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, runtime_data.coordinator.server_device_identifier)}
        )
        if device:
            device_registry.async_remove_device(device.id)

        # Unregister services and clean up shared managers if this is the last config entry
        remaining_entries = hass.config_entries.async_entries(DOMAIN)
        if len(remaining_entries) <= 1:  # Only this entry being unloaded remains
            async_unload_services(hass)
            # Clean up shared managers to prevent memory leaks
            hass.data.pop(f"{DOMAIN}_config_manager", None)
            hass.data.pop(f"{DOMAIN}_binding_manager", None)

    return unload_ok


def async_unload_services(hass: HomeAssistant) -> None:
    """Unload VU1 services."""
    hass.services.async_remove(DOMAIN, SERVICE_SET_DIAL_VALUE)
    hass.services.async_remove(DOMAIN, SERVICE_SET_DIAL_BACKLIGHT)
    hass.services.async_remove(DOMAIN, SERVICE_SET_DIAL_NAME)
    hass.services.async_remove(DOMAIN, SERVICE_SET_DIAL_IMAGE)
    hass.services.async_remove(DOMAIN, SERVICE_RELOAD_DIAL)
    hass.services.async_remove(DOMAIN, SERVICE_CALIBRATE_DIAL)


def _resolve_dial_uids_from_call(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve dial UIDs from a service call's target devices.

    Handles all target selector types: device_id, entity_id, area_id,
    floor_id (resolved via areas), and label_id (resolved via device labels).
    """
    device_registry = dr.async_get(hass)
    device_ids: set[str] = set()

    # Direct device_id targets
    raw_device_ids = call.data.get("device_id", [])
    if isinstance(raw_device_ids, str):
        raw_device_ids = [raw_device_ids]
    device_ids.update(raw_device_ids)

    # Resolve entity_id targets to device_ids
    raw_entity_ids = call.data.get("entity_id", [])
    if isinstance(raw_entity_ids, str):
        raw_entity_ids = [raw_entity_ids]
    if raw_entity_ids:
        entity_registry = er.async_get(hass)
        for entity_id in raw_entity_ids:
            entry = entity_registry.async_get(entity_id)
            if entry and entry.device_id:
                device_ids.add(entry.device_id)
            else:
                _LOGGER.warning("Entity %s not found or has no device, skipping", entity_id)

    # Resolve area_id targets to device_ids
    raw_area_ids = call.data.get("area_id", [])
    if isinstance(raw_area_ids, str):
        raw_area_ids = [raw_area_ids]

    # Resolve floor_id targets to area_ids first
    raw_floor_ids = call.data.get("floor_id", [])
    if isinstance(raw_floor_ids, str):
        raw_floor_ids = [raw_floor_ids]
    if raw_floor_ids:
        area_registry = ar.async_get(hass)
        for floor_id in raw_floor_ids:
            for area_entry in ar.async_entries_for_floor(area_registry, floor_id):
                raw_area_ids.append(area_entry.id)

    for area_id in raw_area_ids:
        for device in dr.async_entries_for_area(device_registry, area_id):
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry and entry.domain == DOMAIN:
                    device_ids.add(device.id)
                    break

    # Resolve label_id targets to device_ids
    raw_label_ids = call.data.get("label_id", [])
    if isinstance(raw_label_ids, str):
        raw_label_ids = [raw_label_ids]
    for label_id in raw_label_ids:
        for device in dr.async_entries_for_label(device_registry, label_id):
            for entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry and entry.domain == DOMAIN:
                    device_ids.add(device.id)
                    break

    if not device_ids:
        raise ServiceValidationError("No target device specified")

    # Resolve device_ids to dial_uids
    dial_uids = []
    for device_id in device_ids:
        device = device_registry.async_get(device_id)
        if not device:
            _LOGGER.warning("Device %s not found, skipping", device_id)
            continue
        for identifier in device.identifiers:
            if identifier[0] == DOMAIN and not identifier[1].startswith("vu1_server_"):
                dial_uids.append(identifier[1])
                break
        else:
            _LOGGER.warning("Device %s is not a VU1 dial, skipping", device_id)

    if not dial_uids:
        raise ServiceValidationError("No valid VU1 dial devices in target selection")
    return dial_uids


def _get_dial_client_and_coordinator(hass: HomeAssistant, dial_uid: str) -> tuple[VU1APIClient, VU1DataUpdateCoordinator] | None:
    """Find the correct client and coordinator for a dial."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
            continue
        coord = entry.runtime_data.coordinator
        if coord.data and dial_uid in coord.data.get("dials", {}):
            return entry.runtime_data.client, coord
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


async def _execute_dial_service_for_all(
    hass: HomeAssistant,
    dial_uids: list[str],
    action_name: str,
    api_call_factory,
    refresh: bool = True,
) -> None:
    """Execute a dial service across multiple dials, collecting errors.

    Attempts every dial even if some fail. Raises a single
    HomeAssistantError at the end listing which dials failed.
    """
    errors: dict[str, Exception] = {}
    for dial_uid in dial_uids:
        try:
            await _execute_dial_service(
                hass, dial_uid, action_name,
                api_call_factory(dial_uid), refresh=refresh,
            )
        except Exception as err:  # noqa: BLE001
            errors[dial_uid] = err

    if errors:
        failed = ", ".join(f"{uid}: {err}" for uid, err in errors.items())
        raise HomeAssistantError(
            f"Failed to {action_name} for {len(errors)}/{len(dial_uids)} dial(s): {failed}"
        )


# Shared schema fields for service target selectors.
# When services.yaml declares `target:`, HA merges device_id/entity_id/area_id/etc
# into call.data. The schema must allow these keys or vol.Schema(PREVENT_EXTRA) rejects them.
_TARGET_SCHEMA_FIELDS = {
    vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("entity_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("area_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("floor_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("label_id"): vol.All(cv.ensure_list, [cv.string]),
}


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for VU1 integration."""
    # Only register services once (check if already registered)
    if hass.services.has_service(DOMAIN, SERVICE_SET_DIAL_VALUE):
        return

    async def set_dial_value(call: ServiceCall) -> None:
        """Set dial value service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        value = call.data[ATTR_VALUE]
        await _execute_dial_service_for_all(
            hass, dial_uids, "set dial value",
            lambda uid: (lambda client: client.set_dial_value(uid, value)),
        )

    async def set_dial_backlight(call: ServiceCall) -> None:
        """Set dial backlight service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        red = call.data[ATTR_RED]
        green = call.data[ATTR_GREEN]
        blue = call.data[ATTR_BLUE]
        await _execute_dial_service_for_all(
            hass, dial_uids, "set dial backlight",
            lambda uid: (lambda client: client.set_dial_backlight(uid, red, green, blue)),
        )

    async def set_dial_name(call: ServiceCall) -> None:
        """Set dial name service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        if len(dial_uids) > 1:
            raise ServiceValidationError(
                "set_dial_name only supports a single target device. "
                "Setting the same name on multiple dials is not supported."
            )
        dial_uid = dial_uids[0]
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

        dial_uids = _resolve_dial_uids_from_call(hass, call)
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

            # Upload to VU1 dial(s)
            await _execute_dial_service_for_all(
                hass, dial_uids, "set dial image",
                lambda uid: (lambda client: client.set_dial_image(uid, image_data, content_type)),
            )

        except Exception as err:
            _LOGGER.error("Failed to set dial image: %s", err)
            raise

    async def reload_dial(call: ServiceCall) -> None:
        """Reload dial service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        await _execute_dial_service_for_all(
            hass, dial_uids, "reload dial",
            lambda uid: (lambda client: client.reload_dial(uid)),
        )

    async def calibrate_dial(call: ServiceCall) -> None:
        """Calibrate dial service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        await _execute_dial_service_for_all(
            hass, dial_uids, "calibrate dial",
            lambda uid: (lambda client: client.calibrate_dial(uid)),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DIAL_VALUE,
        set_dial_value,
        schema=vol.Schema(
            {
                **_TARGET_SCHEMA_FIELDS,
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
                **_TARGET_SCHEMA_FIELDS,
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
                **_TARGET_SCHEMA_FIELDS,
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
                **_TARGET_SCHEMA_FIELDS,
                vol.Required(ATTR_MEDIA_CONTENT_ID): cv.string,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD_DIAL,
        reload_dial,
        schema=vol.Schema(
            {
                **_TARGET_SCHEMA_FIELDS,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE_DIAL,
        calibrate_dial,
        schema=vol.Schema(
            {
                **_TARGET_SCHEMA_FIELDS,
            }
        ),
    )
