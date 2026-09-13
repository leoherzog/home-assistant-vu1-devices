"""Device actions for VU1 dials."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import (
    BEHAVIOR_PRESETS,
    CONF_BACKLIGHT_COLOR,
    CONF_BACKLIGHT_EASING,
    CONF_BOUND_ENTITY,
    CONF_DIAL_EASING,
    CONF_UPDATE_MODE,
    CONF_VALUE_MAX,
    CONF_VALUE_MIN,
    DOMAIN,
    UPDATE_MODE_AUTOMATIC,
    UPDATE_MODE_MANUAL,
)
from .coordinator import _get_dial_client_and_coordinator
from .device_config import async_get_config_manager
from .sensor_binding import BINDING_KEYS, async_get_binding_manager

__all__ = [
    "ACTION_SCHEMA",
    "async_call_action_from_config",
    "async_get_action_capabilities",
    "async_get_actions",
]

ACTION_CONFIGURE_DIAL = "configure_dial"

# Entities that make sense to bind a dial to (numeric value sources).
_BINDABLE_ENTITY_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["sensor", "input_number", "number", "counter"])
)


# Optional configuration fields shared by the action schema and its capabilities.
# No ``default=`` here on purpose: an action must only apply the keys the user
# actually configured, otherwise unrelated dial settings get silently reset.
_CONFIGURE_DIAL_FIELDS = {
    vol.Optional(CONF_BOUND_ENTITY): _BINDABLE_ENTITY_SELECTOR,
    vol.Optional(CONF_VALUE_MIN): vol.Coerce(float),
    vol.Optional(CONF_VALUE_MAX): vol.Coerce(float),
    vol.Optional(CONF_BACKLIGHT_COLOR): selector.ColorRGBSelector(),
    vol.Optional(CONF_DIAL_EASING): vol.In(list(BEHAVIOR_PRESETS)),
    vol.Optional(CONF_BACKLIGHT_EASING): vol.In(list(BEHAVIOR_PRESETS)),
    vol.Optional(CONF_UPDATE_MODE): vol.In([UPDATE_MODE_AUTOMATIC, UPDATE_MODE_MANUAL]),
}

# The device-automation framework validates actions via ``platform.ACTION_SCHEMA``
# (built on ``cv.DEVICE_ACTION_BASE_SCHEMA``, which supplies CONF_DEVICE_ID/CONF_DOMAIN).
ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): ACTION_CONFIGURE_DIAL,
        **_CONFIGURE_DIAL_FIELDS,
    }
)


async def async_get_actions(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List device actions for VU1 dials."""
    actions = []
    
    # Check if this device is a VU1 dial
    dial_uid = await _get_dial_uid_for_device(hass, device_id)
    if dial_uid:
        actions.append({
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: ACTION_CONFIGURE_DIAL,
        })

    return actions


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context,
) -> None:
    """Execute a device action."""
    await _async_configure_dial(hass, config)


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, Any]:
    """Get action capabilities."""
    return {"extra_fields": vol.Schema(_CONFIGURE_DIAL_FIELDS)}


async def _async_configure_dial(hass: HomeAssistant, config: ConfigType) -> None:
    """Configure a VU1 dial."""
    device_id = config[CONF_DEVICE_ID]
    dial_uid = await _get_dial_uid_for_device(hass, device_id)
    
    if not dial_uid:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_a_dial", translation_placeholders={"device_id": device_id})
    
    result = _get_dial_client_and_coordinator(hass, dial_uid)
    if not result:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="dial_not_found", translation_placeholders={"dial_uid": dial_uid})
    client, coordinator = result

    config_manager = async_get_config_manager(hass)
    # The backlight is persisted by async_set_backlight
    dial_config = {
        **config_manager.get_dial_config(dial_uid),
        **{key: config[key] for key in BINDING_KEYS if key in config},
    }
    if dial_config[CONF_VALUE_MIN] >= dial_config[CONF_VALUE_MAX]:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="value_min_not_less_than_max")

    easing = [
        (f"{kind}_easing_period", f"{kind}_easing_step", BEHAVIOR_PRESETS[config[key]], setter)
        for kind, key, setter in (
            ("dial", CONF_DIAL_EASING, client.set_dial_easing),
            ("backlight", CONF_BACKLIGHT_EASING, client.set_backlight_easing),
        )
        if key in config
    ]
    for period, step, preset, _ in easing:
        dial_config[period] = preset[period]
        dial_config[step] = preset[step]

    if easing:
        coordinator.mark_behavior_change_from_ha(dial_uid)
    await config_manager.async_update_dial_config(dial_uid, dial_config)

    if any(key in config for key in BINDING_KEYS):
        await async_get_binding_manager(hass).async_reconfigure_dial_binding(dial_uid)

    if CONF_BACKLIGHT_COLOR in config:
        # The selector yields RGB 0-255; the dial takes 0-100 and keeps its stored white.
        await coordinator.async_set_backlight(
            dial_uid, *(round(c * 100 / 255) for c in config[CONF_BACKLIGHT_COLOR])
        )

    for period, step, preset, setter in easing:
        await setter(dial_uid, preset[period], preset[step])


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
