"""Device actions for VU1 dials."""
import logging
from typing import Any, Dict, List

import voluptuous as vol

from homeassistant.core import HomeAssistant, Context
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import (
    DOMAIN,
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

ACTION_CONFIGURE_DIAL = "configure_dial"

CONFIGURE_DIAL_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("type"): ACTION_CONFIGURE_DIAL,
        vol.Required("device_id"): cv.string,
        vol.Optional(CONF_BOUND_ENTITY): cv.entity_id,
        vol.Optional(CONF_VALUE_MIN, default=DEFAULT_VALUE_MIN): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_VALUE_MAX, default=DEFAULT_VALUE_MAX): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional(CONF_BACKLIGHT_COLOR, default=DEFAULT_BACKLIGHT_COLOR): vol.All(
            vol.Length(min=3, max=3),
            [vol.All(vol.Coerce(int), vol.Range(min=0, max=100))],
        ),
        vol.Optional(CONF_DIAL_EASING, default="linear"): cv.string,
        vol.Optional(CONF_BACKLIGHT_EASING, default="linear"): cv.string,
        vol.Optional(CONF_UPDATE_MODE, default="manual"): vol.In([UPDATE_MODE_AUTOMATIC, "manual"]),
    }
)


async def async_get_actions(hass: HomeAssistant, device_id: str) -> List[Dict[str, Any]]:
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
        raise ValueError(f"Unknown action type: {action_type}")


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> Dict[str, Any]:
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
                vol.Optional(CONF_VALUE_MIN): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(CONF_VALUE_MAX): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(CONF_BACKLIGHT_COLOR): vol.All(
                    vol.Length(min=3, max=3),
                    [vol.All(vol.Coerce(int), vol.Range(min=0, max=100))],
                ),
                vol.Optional(CONF_DIAL_EASING): vol.In(["linear", "ease-in", "ease-out", "ease-in-out"]),
                vol.Optional(CONF_BACKLIGHT_EASING): vol.In(["linear", "ease-in", "ease-out", "ease-in-out"]),
                vol.Optional(CONF_UPDATE_MODE): vol.In([UPDATE_MODE_AUTOMATIC, "manual"]),
            })
        }
    
    return {}


async def _async_configure_dial(hass: HomeAssistant, config: ConfigType) -> None:
    """Configure a VU1 dial."""
    device_id = config["device_id"]
    dial_uid = await _get_dial_uid_for_device(hass, device_id)
    
    if not dial_uid:
        raise ValueError(f"Device {device_id} is not a VU1 dial")
    
    # Prepare configuration
    dial_config = {}
    
    if CONF_BOUND_ENTITY in config:
        dial_config[CONF_BOUND_ENTITY] = config[CONF_BOUND_ENTITY]
    
    if CONF_VALUE_MIN in config:
        dial_config[CONF_VALUE_MIN] = config[CONF_VALUE_MIN]
    
    if CONF_VALUE_MAX in config:
        dial_config[CONF_VALUE_MAX] = config[CONF_VALUE_MAX]
    
    if CONF_BACKLIGHT_COLOR in config:
        dial_config[CONF_BACKLIGHT_COLOR] = config[CONF_BACKLIGHT_COLOR]
    
    if CONF_DIAL_EASING in config:
        dial_config[CONF_DIAL_EASING] = config[CONF_DIAL_EASING]
    
    if CONF_BACKLIGHT_EASING in config:
        dial_config[CONF_BACKLIGHT_EASING] = config[CONF_BACKLIGHT_EASING]
    
    if CONF_UPDATE_MODE in config:
        dial_config[CONF_UPDATE_MODE] = config[CONF_UPDATE_MODE]
    
    # Update configuration
    from .device_config import async_get_config_manager
    config_manager = async_get_config_manager(hass)
    await config_manager.async_update_dial_config(dial_uid, dial_config)
    
    _LOGGER.info("Updated configuration for dial %s", dial_uid)


async def _get_dial_uid_for_device(hass: HomeAssistant, device_id: str) -> str:
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