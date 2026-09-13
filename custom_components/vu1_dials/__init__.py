"""The VU1 Dials integration."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import media_source
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import (
    EVENT_DEVICE_REGISTRY_UPDATED,
    EventDeviceRegistryUpdatedData,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_BLUE,
    ATTR_GREEN,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_NAME,
    ATTR_RED,
    ATTR_VALUE,
    ATTR_WHITE,
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    DATA_BINDING_MANAGER,
    DATA_CONFIG_MANAGER,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_RELOAD_DIAL,
    SERVICE_SET_DIAL_BACKLIGHT,
    SERVICE_SET_DIAL_IMAGE,
    SERVICE_SET_DIAL_NAME,
    SERVICE_SET_DIAL_VALUE,
)
from .coordinator import VU1DataUpdateCoordinator, _get_dial_client_and_coordinator
from .device_config import STORAGE_KEY, STORAGE_VERSION, VU1DialConfigManager
from .sensor_binding import VU1SensorBindingManager, async_switch_to_manual
from .vu1_api import DEFAULT_TIMEOUT, VU1APIClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

__all__ = [
    "VU1ConfigEntry",
    "VU1DataUpdateCoordinator",
    "VU1RuntimeData",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
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
    config_manager = VU1DialConfigManager(hass)
    await config_manager.async_load()
    hass.data[DATA_CONFIG_MANAGER] = config_manager
    hass.data[DATA_BINDING_MANAGER] = VU1SensorBindingManager(hass)
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
    """Set up VU1 Dials from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    api_key = entry.data[CONF_API_KEY]
    timeout = entry.options.get("timeout", DEFAULT_TIMEOUT)

    session = async_get_clientsession(hass)
    client = VU1APIClient(host, port, api_key, session=session, timeout=timeout)

    update_interval = timedelta(
        seconds=entry.options.get("update_interval", DEFAULT_UPDATE_INTERVAL)
    )

    coordinator = VU1DataUpdateCoordinator(hass, client, update_interval, config_entry=entry)

    binding_manager = hass.data[DATA_BINDING_MANAGER]

    # Register the VU1 server as a hub device
    coordinator.hub_device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.server_device_identifier)},
        manufacturer="Streacom",
        model="VU1 Server",
        name=f"VU1 Server ({host}:{port})",
    ).id

    await coordinator.async_config_entry_first_refresh()

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

    # The first refresh ran before runtime_data existed, so bindings could not reach the client yet.
    await binding_manager.async_update_bindings(coordinator.data)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        ir.async_delete_issue(hass, DOMAIN, "no_dials")
        runtime_data = entry.runtime_data
        coordinator = runtime_data.coordinator
        if coordinator.data:
            for dial_uid in list(coordinator.data.get("dials", {}).keys()):
                await runtime_data.binding_manager.async_remove_binding(dial_uid)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: VU1ConfigEntry) -> None:
    """Discard persisted dial configuration when the entry is removed."""
    if config_manager := hass.data.get(DATA_CONFIG_MANAGER):
        await config_manager.async_remove()
    else:
        await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()


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
    runtime_data = getattr(config_entry, "runtime_data", None)
    known_dials = (
        runtime_data.coordinator.data["dials"]
        if runtime_data and runtime_data.coordinator.data
        else {}
    )

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
        await hass.data[DATA_CONFIG_MANAGER].async_remove_dial_config(identifier)
        # Reload so every platform re-adds the dial's entities if it comes back.
        if config_entry.state is ConfigEntryState.LOADED:
            hass.config_entries.async_schedule_reload(config_entry.entry_id)

    return True


def _resolve_dial_uids_from_call(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve dial UIDs from a service call's target selection.

    A dial is targeted through its resolved entities, so an area only reaches a
    dial whose entities are in it, or through an explicitly targeted device.
    """
    target = TargetSelection(call.data)
    selected = async_extract_referenced_entity_ids(hass, target)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    selected.log_missing(
        {entity_id for entity_id in selected.referenced if hass.states.get(entity_id) is None},
        _LOGGER,
    )
    device_ids = target.device_ids | {
        entity.device_id
        for entity_id in selected.referenced | selected.indirectly_referenced
        if (entity := entity_registry.async_get(entity_id)) and entity.device_id
    }

    dial_uids = [
        identifier
        for device_id in device_ids
        if (device := device_registry.async_get(device_id))
        for domain, identifier in device.identifiers
        if domain == DOMAIN and not identifier.startswith("vu1_server_")
    ]

    if not dial_uids:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_dials_targeted")
    return dial_uids


async def _execute_dial_service_for_all(
    hass: HomeAssistant,
    dial_uids: list[str],
    action_name: str,
    api_call: Callable[[VU1DataUpdateCoordinator, str], Awaitable[None]],
    refresh: bool = True,
) -> None:
    """Run ``api_call(coordinator, uid)`` for every dial concurrently, then refresh once if ``refresh``.

    Raises a single error listing which dials failed.
    """

    async def run(uid: str) -> VU1DataUpdateCoordinator:
        if not (result := _get_dial_client_and_coordinator(hass, uid)):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="dial_not_found", translation_placeholders={"dial_uid": uid})
        await api_call(result[1], uid)
        return result[1]

    results = await asyncio.gather(*(run(uid) for uid in dial_uids), return_exceptions=True)
    errors = {uid: result for uid, result in zip(dial_uids, results) if isinstance(result, Exception)}
    if refresh and (coordinator := next((r for r in results if isinstance(r, VU1DataUpdateCoordinator)), None)):
        await coordinator.async_request_refresh()

    if errors:
        failed = ", ".join(f"{uid}: {err}" for uid, err in errors.items())
        error_cls = (
            ServiceValidationError
            if all(isinstance(err, ServiceValidationError) for err in errors.values())
            else HomeAssistantError
        )
        raise error_cls(
            translation_domain=DOMAIN, translation_key="action_failed",
            translation_placeholders={
                "action": action_name,
                "failed": str(len(errors)),
                "total": str(len(dial_uids)),
                "errors": failed,
            },
        )


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for VU1 integration."""

    async def set_dial_value(call: ServiceCall) -> None:
        """Set dial value service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        value = call.data[ATTR_VALUE]

        async def set_value(coordinator: VU1DataUpdateCoordinator, uid: str) -> None:
            await async_switch_to_manual(hass, uid)
            await coordinator.client.set_dial_value(uid, value)

        await _execute_dial_service_for_all(hass, dial_uids, "set dial value", set_value)

    async def set_dial_backlight(call: ServiceCall) -> None:
        """Set dial backlight service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        red = call.data[ATTR_RED]
        green = call.data[ATTR_GREEN]
        blue = call.data[ATTR_BLUE]
        white = call.data.get(ATTR_WHITE)
        await _execute_dial_service_for_all(
            hass, dial_uids, "set dial backlight",
            lambda coordinator, uid: coordinator.async_set_backlight(uid, red, green, blue, white),
            refresh=False,
        )

    async def set_dial_name(call: ServiceCall) -> None:
        """Set dial name service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        if len(dial_uids) > 1:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="single_dial_only")
        dial_uid = dial_uids[0]
        name = call.data[ATTR_NAME]

        result = _get_dial_client_and_coordinator(hass, dial_uid)
        if not result:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="dial_not_found", translation_placeholders={"dial_uid": dial_uid})

        _client, coordinator = result
        await coordinator.async_set_dial_name(dial_uid, name)

    async def set_dial_image(call: ServiceCall) -> None:
        """Set dial background image service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        # The `media` selector in services.yaml emits a dict
        # ({media_content_id, media_content_type, metadata}); plain string calls
        # (e.g. from YAML) pass the URI directly. Unwrap either form.
        media_value = call.data[ATTR_MEDIA_CONTENT_ID]
        media_content_id = (
            media_value["media_content_id"]
            if isinstance(media_value, dict)
            else media_value
        )
        resolved_media = await media_source.async_resolve_media(hass, media_content_id, None)
        if resolved_media.path is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_local_media", translation_placeholders={"media_content_id": media_content_id})
        try:
            image_data = await hass.async_add_executor_job(resolved_media.path.read_bytes)
        except OSError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="cannot_read_media", translation_placeholders={"path": str(resolved_media.path), "error": str(err)}) from err

        await _execute_dial_service_for_all(
            hass, dial_uids, "set dial image",
            lambda coordinator, uid: coordinator.client.set_dial_image(uid, image_data),
        )

    async def reload_dial(call: ServiceCall) -> None:
        """Reload dial service."""
        dial_uids = _resolve_dial_uids_from_call(hass, call)
        await _execute_dial_service_for_all(
            hass, dial_uids, "reload dial",
            lambda coordinator, uid: coordinator.client.reload_dial(uid),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DIAL_VALUE,
        set_dial_value,
        schema=vol.Schema(
            {
                **cv.TARGET_SERVICE_FIELDS,
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
                **cv.TARGET_SERVICE_FIELDS,
                vol.Required(ATTR_RED): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Required(ATTR_GREEN): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Required(ATTR_BLUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Optional(ATTR_WHITE): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_DIAL_NAME,
        set_dial_name,
        schema=vol.Schema(
            {
                **cv.TARGET_SERVICE_FIELDS,
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
                **cv.TARGET_SERVICE_FIELDS,
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
        schema=vol.Schema(cv.TARGET_SERVICE_FIELDS),
    )
