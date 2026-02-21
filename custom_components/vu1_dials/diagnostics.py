"""Diagnostics support for VU1 Dials integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import VU1ConfigEntry
from .const import DOMAIN
from .device_config import async_get_config_manager

TO_REDACT = {
    "api_key",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: VU1ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    coordinator = runtime_data.coordinator

    # Collect coordinator data
    coordinator_data = coordinator.data or {}

    # Collect dial configuration from config manager
    config_manager = async_get_config_manager(hass)

    dial_configs: dict[str, Any] = {}
    for dial_uid in coordinator_data.get("dials", {}).keys():
        dial_configs[dial_uid] = config_manager.get_dial_config(dial_uid)

    # Build diagnostics payload
    diagnostics_data: dict[str, Any] = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "server_device_identifier": coordinator.server_device_identifier,
        },
        "dials": {},
        "dial_configs": async_redact_data(dial_configs, TO_REDACT),
    }

    # Add per-dial information (redact sensitive data)
    for dial_uid, dial_data in coordinator_data.get("dials", {}).items():
        diagnostics_data["dials"][dial_uid] = {
            "dial_name": dial_data.get("dial_name"),
            "image_file": dial_data.get("image_file"),
            "detailed_status": dial_data.get("detailed_status", {}),
        }

    # Add binding manager state
    binding_manager = runtime_data.binding_manager
    if binding_manager:
        bindings_info: dict[str, Any] = {}
        for dial_uid, binding in binding_manager._bindings.items():
            bindings_info[dial_uid] = {
                "entity_id": binding.get("entity_id"),
                "has_last_state": binding.get("last_state") is not None,
            }
        diagnostics_data["sensor_bindings"] = bindings_info

    return diagnostics_data
