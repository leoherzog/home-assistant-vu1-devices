"""Device actions for VU1 dials."""
import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import (
    DOMAIN,
    BEHAVIOR_PRESETS,
    CONF_BOUND_ENTITY,
    CONF_VALUE_MIN,
    CONF_VALUE_MAX,
    CONF_BACKLIGHT_COLOR,
    CONF_DIAL_EASING,
    CONF_BACKLIGHT_EASING,
    CONF_UPDATE_MODE,
    UPDATE_MODE_AUTOMATIC,
    DEFAULT_VALUE_MIN,
    DEFAULT_VALUE_MAX,
    DEFAULT_BACKLIGHT_COLOR,
)

_LOGGER = logging.getLogger(__name__)

# Derive the (period, step) tuple format from the canonical BEHAVIOR_PRESETS
EASING_PRESETS = {
    key: {
        "dial": (p["dial_easing_period"], p["dial_easing_step"]),
        "backlight": (p["backlight_easing_period"], p["backlight_easing_step"]),
    }
    for key, p in BEHAVIOR_PRESETS.items()
    if "dial_easing_period" in p  # Skip "custom" which has no numeric values
}

__all__ = ["async_get_actions", "async_call_action_from_config", "async_get_action_capabilities"]

ACTION_CONFIGURE_DIAL = "configure_dial"


def validate_min_max_range(config):
    """Validate that value_min < value_max if both are provided."""
    value_min = config.get(CONF_VALUE_MIN)
    value_max = config.get(CONF_VALUE_MAX)
    
    if value_min is not None and value_max is not None and value_min >= value_max:
        raise vol.Invalid(f"value_min ({value_min}) must be less than value_max ({value_max})")
    
    return config

CONFIGURE_DIAL_ACTION_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("type"): ACTION_CONFIGURE_DIAL,
            vol.Required("device_id"): cv.string,
            vol.Optional(CONF_BOUND_ENTITY): cv.entity_id,
            vol.Optional(CONF_VALUE_MIN, default=DEFAULT_VALUE_MIN): vol.Coerce(float),
            vol.Optional(CONF_VALUE_MAX, default=DEFAULT_VALUE_MAX): vol.Coerce(float),
            vol.Optional(CONF_BACKLIGHT_COLOR, default=DEFAULT_BACKLIGHT_COLOR): vol.All(
                vol.Length(min=3, max=3),
                [vol.All(vol.Coerce(int), vol.Range(min=0, max=100))],
            ),
            vol.Optional(CONF_DIAL_EASING, default="balanced"): vol.In(list(EASING_PRESETS.keys())),
            vol.Optional(CONF_BACKLIGHT_EASING, default="balanced"): vol.In(list(EASING_PRESETS.keys())),
            vol.Optional(CONF_UPDATE_MODE, default="manual"): vol.In([UPDATE_MODE_AUTOMATIC, "manual"]),
        }
    ),
    validate_min_max_range,
)


async def async_get_actions(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List device actions for VU1 dials."""
    actions = []
    
    # Check if this device is a VU1 dial
    dial_uid = await _get_dial_uid_for_device(hass, device_id)
    if dial_uid:
        actions.append({
            "type": ACTION_CONFIGURE_DIAL,
            "device_id": device_id,
        })
    
    return actions


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context,
) -> None:
    """Execute a device action."""
    action_type = config["type"]
    
    if action_type == ACTION_CONFIGURE_DIAL:
        await _async_configure_dial(hass, config)
    else:
        raise HomeAssistantError(f"Unknown action type: {action_type}")


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, Any]:
    """Get action capabilities."""
    action_type = config["type"]
    
    if action_type == ACTION_CONFIGURE_DIAL:
        # Get available entities for binding
        entity_registry = er.async_get(hass)
        entities = []
        
        for entity in entity_registry.entities.values():
            # Include sensors and other numeric entities
            if entity.domain in ["sensor", "input_number", "number"]:
                entities.append({
                    "value": entity.entity_id,
                    "label": f"{entity.entity_id} ({entity.name or entity.entity_id})",
                })
        
        return {
            "extra_fields": vol.Schema({
                vol.Optional(CONF_BOUND_ENTITY): vol.In([e["value"] for e in entities]),
                vol.Optional(CONF_VALUE_MIN): vol.Coerce(float),
                vol.Optional(CONF_VALUE_MAX): vol.Coerce(float),
                vol.Optional(CONF_BACKLIGHT_COLOR): vol.All(
                    vol.Length(min=3, max=3),
                    [vol.All(vol.Coerce(int), vol.Range(min=0, max=100))],
                ),
                vol.Optional(CONF_DIAL_EASING): vol.In(list(EASING_PRESETS.keys())),
                vol.Optional(CONF_BACKLIGHT_EASING): vol.In(list(EASING_PRESETS.keys())),
                vol.Optional(CONF_UPDATE_MODE): vol.In([UPDATE_MODE_AUTOMATIC, "manual"]),
            })
        }
    
    return {}


async def _async_configure_dial(hass: HomeAssistant, config: ConfigType) -> None:
    """Configure a VU1 dial."""
    device_id = config["device_id"]
    dial_uid = await _get_dial_uid_for_device(hass, device_id)
    
    if not dial_uid:
        raise HomeAssistantError(f"Device {device_id} is not a VU1 dial")
    
    # Extract configuration keys from action config
    config_keys = [
        CONF_BOUND_ENTITY,
        CONF_VALUE_MIN,
        CONF_VALUE_MAX,
        CONF_BACKLIGHT_COLOR,
        CONF_UPDATE_MODE,
    ]
    dial_config = {key: config[key] for key in config_keys if key in config}

    # Map preset names to numeric easing values for storage/hardware
    dial_period = dial_step = None
    backlight_period = backlight_step = None

    dial_preset = config.get(CONF_DIAL_EASING)
    if dial_preset in EASING_PRESETS:
        dial_period, dial_step = EASING_PRESETS[dial_preset]["dial"]
        dial_config["dial_easing_period"] = dial_period
        dial_config["dial_easing_step"] = dial_step

    backlight_preset = config.get(CONF_BACKLIGHT_EASING)
    if backlight_preset in EASING_PRESETS:
        backlight_period, backlight_step = EASING_PRESETS[backlight_preset]["backlight"]
        dial_config["backlight_easing_period"] = backlight_period
        dial_config["backlight_easing_step"] = backlight_step
    
    # Update configuration
    from .device_config import async_get_config_manager
    config_manager = async_get_config_manager(hass)
    await config_manager.async_update_dial_config(dial_uid, dial_config)
    
    # Get the client and coordinator to apply changes immediately
    from . import _get_dial_client_and_coordinator

    result = _get_dial_client_and_coordinator(hass, dial_uid)
    if not result:
        _LOGGER.error("Could not find client/coordinator for dial %s", dial_uid)
        return
    client, coordinator = result
    
    # Apply changes to physical device immediately
    try:
        # Apply backlight color if specified
        if CONF_BACKLIGHT_COLOR in config:
            backlight_color = config[CONF_BACKLIGHT_COLOR]
            await client.set_dial_backlight(dial_uid, backlight_color[0], backlight_color[1], backlight_color[2])
            _LOGGER.debug("Applied backlight color %s to dial %s", backlight_color, dial_uid)
        
        # Apply easing settings if specified - use preset values
        if dial_period is not None and dial_step is not None:
            coordinator.mark_behavior_change_from_ha(dial_uid)
            await client.set_dial_easing(dial_uid, dial_period, dial_step)
            _LOGGER.debug(
                "Applied dial easing preset '%s' to dial %s: period=%s, step=%s",
                dial_preset,
                dial_uid,
                dial_period,
                dial_step,
            )

        if backlight_period is not None and backlight_step is not None:
            coordinator.mark_behavior_change_from_ha(dial_uid)
            await client.set_backlight_easing(dial_uid, backlight_period, backlight_step)
            _LOGGER.debug(
                "Applied backlight easing preset '%s' to dial %s: period=%s, step=%s",
                backlight_preset,
                dial_uid,
                backlight_period,
                backlight_step,
            )
        
        # Update sensor bindings if binding-related keys changed
        binding_keys = {CONF_BOUND_ENTITY, CONF_VALUE_MIN, CONF_VALUE_MAX, CONF_UPDATE_MODE}
        if any(key in config for key in binding_keys):
            from .sensor_binding import async_get_binding_manager
            binding_manager = async_get_binding_manager(hass)
            if binding_manager:
                await binding_manager.async_reconfigure_dial_binding(dial_uid)
                _LOGGER.debug("Updated sensor binding for dial %s", dial_uid)
        
        # Request coordinator refresh to update state
        await coordinator.async_request_refresh()
        
    except Exception as err:
        _LOGGER.error("Failed to apply device action changes to dial %s: %s", dial_uid, err)
        raise
    
    _LOGGER.info("Updated and applied configuration for dial %s", dial_uid)


async def _get_dial_uid_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Get dial UID for a device ID."""
    from homeassistant.helpers import device_registry as dr
    
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device:
        return None
    
    # Check if this device has VU1 dial identifiers
    for identifier_type, identifier_value in device.identifiers:
        if identifier_type == DOMAIN and not identifier_value.startswith("vu1_server_"):
            # This should be a dial UID
            return identifier_value
    
    return None


# Register the action schema
DEVICE_ACTION_SCHEMA = vol.Any(CONFIGURE_DIAL_ACTION_SCHEMA)
