"""Diagnostics support for VU1 Dials integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from . import VU1ConfigEntry
from .const import CONF_API_KEY, CONF_HOST
from .device_config import async_get_config_manager

TO_REDACT = {CONF_API_KEY, CONF_HOST}


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

    dial_configs = {
        dial_uid: config_manager.get_dial_config(dial_uid)
        for dial_uid in coordinator_data.get("dials", {})
    }

    # Build diagnostics payload
    diagnostics_data: dict[str, Any] = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "domain": entry.domain,
            "title": REDACTED,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "server_device_identifier": coordinator.server_device_identifier,
        },
        "dials": {},
        "dial_configs": dial_configs,
    }

    # Add per-dial information (redact sensitive data)
    for dial_uid, dial_data in coordinator_data.get("dials", {}).items():
        diagnostics_data["dials"][dial_uid] = {
            "dial_name": dial_data.get("dial_name"),
            "image_file": dial_data.get("image_file"),
            "detailed_status": dial_data.get("detailed_status", {}),
        }

    diagnostics_data["sensor_bindings"] = runtime_data.binding_manager.async_get_bindings_summary()

    return diagnostics_data
