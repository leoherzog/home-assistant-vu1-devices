"""The VU1 Dials integration."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback, Event
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED, EventDeviceRegistryUpdatedData
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
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
    CONF_ADDON_MANAGED,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_PORT,
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
from .coordinator import VU1DataUpdateCoordinator, _get_dial_client_and_coordinator
from .vu1_api import VU1APIClient, VU1APIError, VU1InvalidNameError, discover_vu1_addon

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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


async def async_migrate_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
    """Migrate config entry to a new version."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version == 1:
        new_data = dict(entry.data)

        # v1 ingress entries stored the Supervisor ingress_port instead of the
        # VU1 Server API port.  Fix the port and drop the unused ingress fields.
        if new_data.pop("ingress", None):
            new_data["port"] = DEFAULT_PORT
            new_data.pop("ingress_slug", None)
            new_data.pop("supervisor_token", None)
            _LOGGER.info(
                "Migrated ingress config entry to direct connection on port %s",
                DEFAULT_PORT,
            )

        hass.config_entries.async_update_entry(entry, data=new_data, version=2)

    if entry.version == 2:
        # v2 keyed the hub device + config-entry unique_id on host:port, which
        # churns when the add-on hostname changes and orphaned the hub device on
        # the Docker-IP migration. Re-key the existing hub device on entry_id
        # (keeping the same device.id so child dials stay linked via via_device)
        # and drop the stale host-based unique_id (duplicate prevention now uses
        # _async_abort_entries_match in the config flow).
        device_registry = dr.async_get(hass)
        new_identifier = f"vu1_server_{entry.entry_id}"
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
            new_identifiers = set()
            needs_update = False
            for domain, ident in device.identifiers:
                if domain == DOMAIN and ident.startswith("vu1_server_") and ident != new_identifier:
                    new_identifiers.add((DOMAIN, new_identifier))
                    needs_update = True
                else:
                    new_identifiers.add((domain, ident))
            if needs_update:
                _LOGGER.info(
                    "Migrating VU1 hub device identifier to entry-id based: %s",
                    new_identifier,
                )
                device_registry.async_update_device(
                    device.id, new_identifiers=new_identifiers
                )

        hass.config_entries.async_update_entry(entry, unique_id=None, version=3)

    _LOGGER.debug("Migration to version %s successful", entry.version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
    """Set up VU1 Dials from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    api_key = entry.data[CONF_API_KEY]
    timeout = entry.options.get("timeout", DEFAULT_TIMEOUT)

    # Migrate entries that stored a Docker IP (172.30.33.x) to the stable DNS
    # hostname returned by the Supervisor API.  The hostname doesn't change
    # across reboots, so this migration only needs to succeed once.
    if host.startswith("172.30.33."):
        discovered = await discover_vu1_addon()
        if discovered and discovered.get("addon_discovered"):
            new_host = discovered["host"]
            new_port = discovered.get("port", port)
            new_data = {
                **entry.data,
                CONF_ADDON_MANAGED: True,
                CONF_HOST: new_host,
                CONF_PORT: new_port,
            }
            _LOGGER.info(
                "Migrating VU1 add-on config from Docker IP to stable hostname: "
                "%s:%s -> %s:%s",
                host, port, new_host, new_port,
            )
            host = new_host
            port = new_port
            hass.config_entries.async_update_entry(entry, data=new_data)

    session = async_get_clientsession(hass)
    client = VU1APIClient(host, port, api_key, session=session, timeout=timeout)
    connection_info = f"{host}:{port}"
    # Hub identity is keyed on the config entry id so it stays stable when the
    # add-on host/port changes (e.g. Docker IP -> DNS hostname migration) and
    # so the same server can't be orphaned/duplicated. The "vu1_server_" prefix
    # is still how dial-vs-hub devices are distinguished elsewhere.
    device_identifier = f"vu1_server_{entry.entry_id}"

    # Connection validation happens during async_config_entry_first_refresh below,
    # whose _async_update_data raises ConfigEntryNotReady if the server is unreachable.

    update_interval = timedelta(
        seconds=entry.options.get("update_interval", DEFAULT_UPDATE_INTERVAL)
    )

    coordinator = VU1DataUpdateCoordinator(hass, client, update_interval, config_entry=entry)
    # Store the device identifier string for proper via_device relationships
    coordinator.server_device_identifier = device_identifier

    # Set up device configuration manager
    from .device_config import async_get_config_manager
    config_manager = async_get_config_manager(hass)
    await config_manager.async_load()

    # Set up sensor binding manager before first data refresh
    from .sensor_binding import async_get_binding_manager
    binding_manager = async_get_binding_manager(hass)

    # Connect binding manager to coordinator
    coordinator.set_binding_manager(binding_manager)
    
    # Register the VU1 server as a hub device
    device_registry = dr.async_get(hass)
    
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
                entry.async_create_background_task(
                    hass,
                    coordinator.async_handle_ha_name_change(dial_uid, new_name),
                    f"vu1_name_change_{dial_uid}",
                )
                break
    
    # Register the device registry listener and bind its lifecycle to config entry
    entry.async_on_unload(
        hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, handle_device_registry_updated)
    )
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if coordinator.data:
        dials_data = coordinator.data.get("dials", {})
        await binding_manager.async_update_bindings({"dials": dials_data}, entry.entry_id)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        runtime_data = entry.runtime_data

        await runtime_data.client.close()

        if runtime_data.binding_manager:
            # Remove only this entry's bindings, not all bindings (binding manager is shared)
            coordinator = runtime_data.coordinator
            if coordinator.data:
                for dial_uid in list(coordinator.data.get("dials", {}).keys()):
                    await runtime_data.binding_manager.async_remove_binding(dial_uid)

        # HA automatically cleans up devices when their config entry is removed.
        # Do NOT manually remove devices here — it destroys user customizations
        # (area, labels, name_by_user) on reload.

        # Clean up shared managers if this is the last config entry.
        # Services are intentionally NOT unregistered here: they are registered
        # once in async_setup (per HA session) and must survive config-entry
        # reloads (reconfigure, options save, manual reload). The handlers raise
        # ServiceValidationError gracefully when no entry/dial is available.
        remaining_entries = hass.config_entries.async_entries(DOMAIN)
        if len(remaining_entries) <= 1:  # Only this entry being unloaded remains
            # Clean up shared managers to prevent memory leaks
            hass.data.pop(f"{DOMAIN}_config_manager", None)
            hass.data.pop(f"{DOMAIN}_binding_manager", None)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deletion of a device from the UI.

    The hub device may never be removed while its entry exists. A dial device
    may only be removed once the dial is gone from the server (otherwise it
    would just be re-created on the next refresh); on removal its persisted
    config is pruned.
    """
    coordinator = config_entry.runtime_data.coordinator
    known_dials = coordinator.data.get("dials", {}) if coordinator.data else {}

    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier.startswith("vu1_server_"):
            # Hub device — keep it for the lifetime of the entry.
            return False
        # Dial device: refuse while the dial is still reported by the server.
        if identifier in known_dials:
            return False
        # Dial is permanently gone — clean up its persisted configuration.
        from .device_config import async_get_config_manager

        config_manager = async_get_config_manager(hass)
        await config_manager.async_remove_dial_config(identifier)

    return True


def _resolve_dial_uids_from_call(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve dial UIDs from a service call's target selection.

    Expands device_id/entity_id/area_id/floor_id/label_id targets via the
    target helper (which correctly handles entities individually assigned to an
    area), then maps the referenced devices back to VU1 dial UIDs.
    """
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    # Devices come directly from device/area/floor/label targets; add the device
    # backing each referenced entity so entity_id targets resolve too.
    device_ids: set[str] = set(selected.referenced_devices)
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entity = entity_registry.async_get(entity_id)
        if entity and entity.device_id:
            device_ids.add(entity.device_id)

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
        raise ServiceValidationError(f"Invalid dial_uid: {dial_uid}")

    result = _get_dial_client_and_coordinator(hass, dial_uid)
    if not result:
        _LOGGER.error("Dial %s not found", dial_uid)
        raise ServiceValidationError(f"Dial {dial_uid} not found")
    
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
    """Execute a dial service across multiple dials concurrently.

    Fires all API calls in parallel, then performs a single coordinator
    refresh per unique coordinator (instead of one per dial).
    Raises a single HomeAssistantError listing which dials failed.
    """
    # Fire all API calls concurrently, suppressing per-dial refresh
    tasks = [
        _execute_dial_service(
            hass, uid, action_name,
            api_call_factory(uid), refresh=False,
        )
        for uid in dial_uids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect errors
    errors: dict[str, Exception] = {}
    for uid, result in zip(dial_uids, results):
        if isinstance(result, Exception):
            errors[uid] = result

    # Single refresh per unique coordinator after all API calls complete
    if refresh:
        refreshed: set[int] = set()
        for uid in dial_uids:
            if uid in errors:
                continue
            pair = _get_dial_client_and_coordinator(hass, uid)
            if pair:
                _, coordinator = pair
                coord_id = id(coordinator)
                if coord_id not in refreshed:
                    refreshed.add(coord_id)
                    await coordinator.async_request_refresh()

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
            raise ServiceValidationError(f"Dial {dial_uid} not found")

        _client, coordinator = result

        try:
            await coordinator.async_set_dial_name(dial_uid, name)
        except VU1InvalidNameError as err:
            raise ServiceValidationError(str(err)) from err
        except Exception as err:
            _LOGGER.error("Service call failed to set dial name for %s: %s", dial_uid, err)
            raise

    async def set_dial_image(call: ServiceCall) -> None:
        """Set dial background image service."""
        from homeassistant.components.media_source import async_resolve_media

        dial_uids = _resolve_dial_uids_from_call(hass, call)
        # The `media` selector in services.yaml emits a dict
        # ({media_content_id, media_content_type, metadata}); plain string calls
        # (e.g. from YAML) pass the URI directly. Unwrap either form.
        media_value = call.data.get(ATTR_MEDIA_CONTENT_ID)
        media_content_id = (
            media_value["media_content_id"]
            if isinstance(media_value, dict)
            else media_value
        )

        if not media_content_id:
            _LOGGER.error("No media content ID provided for dial image")
            raise ServiceValidationError("Media content ID is required")

        try:
            # Resolve the media source URI to get actual file path/data
            _LOGGER.debug("Resolving media content ID: %s", media_content_id)
            resolved_media = await async_resolve_media(hass, media_content_id, None)

            if not resolved_media.url:
                raise ServiceValidationError("Could not resolve media content to URL")

            # Read the image data from the resolved URL
            if resolved_media.url.startswith("file://"):
                # Local file access
                file_path = resolved_media.url[7:]  # Remove 'file://' prefix

                # Use async-friendly file operations to avoid blocking the event loop
                if not await hass.async_add_executor_job(Path(file_path).exists):
                    raise ServiceValidationError(f"Media file not found: {file_path}")

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
                        raise HomeAssistantError(f"Failed to fetch media: HTTP {response.status}")
                    image_data = await response.read()
                    content_type = response.headers.get('content-type', 'image/png')

            if not image_data:
                raise HomeAssistantError("No image data retrieved from media source")

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
                # The `media` selector returns a dict, while YAML/templated calls
                # pass a plain media-source URI string. Accept both.
                vol.Required(ATTR_MEDIA_CONTENT_ID): vol.Any(
                    cv.string,
                    vol.Schema(
                        {vol.Required("media_content_id"): cv.string},
                        extra=vol.ALLOW_EXTRA,
                    ),
                ),
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
