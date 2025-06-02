"""Config flow for VU1 Devices integration."""
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_BOUND_ENTITY, CONF_VALUE_MIN, CONF_VALUE_MAX, CONF_BACKLIGHT_COLOR, CONF_UPDATE_MODE, UPDATE_MODE_AUTOMATIC
from .vu1_api import VU1APIClient, VU1APIError, discover_vu1_server
from .device_config import async_get_config_manager

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("host", default="localhost"): cv.string,
        vol.Required("port", default=5340): cv.port,
        vol.Required("api_key"): cv.string,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VU1 Devices."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._discovered_host: Optional[str] = None
        self._discovered_port: Optional[int] = None
        self._discovery_method: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        # Try auto-discovery first
        if user_input is None:
            discovered = await discover_vu1_server()
            if discovered:
                # Use actual discovered host/port or defaults
                self._discovered_host = discovered.get("host", "localhost")
                self._discovered_port = discovered.get("port", 5340)
                self._discovery_method = "addon" if discovered.get("addon_discovered") else "localhost"
                # Set unique ID to prevent duplicate configs
                await self.async_set_unique_id(f"vu1_server_{self._discovered_host}_{self._discovered_port}")
                self._abort_if_unique_id_configured()
                return await self.async_step_discovery()

        if user_input is not None:
            # Set unique ID to prevent duplicate configs
            await self.async_set_unique_id(f"vu1_server_{user_input['host']}_{user_input['port']}")
            self._abort_if_unique_id_configured()
            
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        # No auto-discovery, show manual form with helpful message
        description_placeholders = {"discovery_info": "No VU1 Server auto-discovered. Please configure manually or ensure the VU1 Server add-on is running."}
        return self.async_show_form(
            step_id="user", 
            data_schema=STEP_USER_DATA_SCHEMA, 
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_discovery(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle discovery step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            # Combine discovered info with user input
            full_input = {
                "host": self._discovered_host or user_input.get("host", "localhost"),
                "port": self._discovered_port or user_input.get("port", 5340),
                "api_key": user_input["api_key"],
            }

            try:
                info = await validate_input(self.hass, full_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Update unique ID with final configuration
                await self.async_set_unique_id(f"vu1_server_{full_input['host']}_{full_input['port']}")
                return self.async_create_entry(title=info["title"], data=full_input)

        # Show form with discovered info pre-filled
        discovery_schema = vol.Schema(
            {
                vol.Required(
                    "host", default=self._discovered_host or "localhost"
                ): cv.string,
                vol.Required(
                    "port", default=self._discovered_port or 5340
                ): cv.port,
                vol.Required("api_key"): cv.string,
            }
        )

        # Add helpful description based on discovery method
        description_placeholders = {}
        if self._discovery_method == "addon":
            description_placeholders["discovery_info"] = f"VU1 Server add-on auto-discovered at {self._discovered_host}:{self._discovered_port}. This uses the internal add-on network and doesn't require exposing ports."
        else:
            description_placeholders["discovery_info"] = f"VU1 Server discovered at {self._discovered_host}:{self._discovered_port}. Verify the server is accessible and ports are properly configured."

        return self.async_show_form(
            step_id="discovery",
            data_schema=discovery_schema,
            errors=errors,
            description_placeholders=description_placeholders,
            description_placeholders={
                "host": self._discovered_host or "localhost",
                "port": str(self._discovered_port or 5340),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self._selected_dial = None

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            if "configure_dial" in user_input:
                self._selected_dial = user_input["configure_dial"]
                return await self.async_step_configure_dial()
            return self.async_create_entry(title="", data=user_input)

        # Get dials for this config entry
        entry_data = self.hass.data[DOMAIN].get(self.config_entry.entry_id, {})
        coordinator = entry_data.get("coordinator")
        dials = coordinator.data if coordinator and coordinator.data else {}
        
        # Create dial selection for configuration
        dial_options = {uid: data.get("dial_name", f"Dial {uid}") for uid, data in dials.items()}
        
        schema_dict = {
            vol.Optional(
                "update_interval",
                default=self.config_entry.options.get("update_interval", 30),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
        }
        
        if dial_options:
            schema_dict[vol.Optional("configure_dial")] = vol.In(dial_options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )


    async def async_step_configure_dial(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure a specific dial."""
        if user_input is not None:
            # Save dial configuration
            config_manager = async_get_config_manager(self.hass)
            bound_entity = user_input.get("bound_entity")
            if bound_entity == "none":
                bound_entity = None
            dial_config = {
                CONF_BOUND_ENTITY: bound_entity,
                CONF_VALUE_MIN: user_input.get("value_min", 0),
                CONF_VALUE_MAX: user_input.get("value_max", 100),
                CONF_UPDATE_MODE: user_input.get("update_mode", "manual"),
                CONF_BACKLIGHT_COLOR: [
                    user_input.get("backlight_red", 100),
                    user_input.get("backlight_green", 100),
                    user_input.get("backlight_blue", 100),
                ],
            }
            await config_manager.async_update_dial_config(self._selected_dial, dial_config)
            return self.async_create_entry(title="", data={})

        # Get current configuration
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(self._selected_dial)
        
        # Get available entities for binding
        from homeassistant.helpers import entity_registry as er
        entity_registry = er.async_get(self.hass)
        entity_options = {"none": "None (Manual control)"}
        
        for entity in entity_registry.entities.values():
            if entity.domain in ["sensor", "input_number", "number"]:
                entity_options[entity.entity_id] = f"{entity.entity_id} ({entity.name or entity.entity_id})"
        
        # Get dial name for display
        entry_data = self.hass.data[DOMAIN].get(self.config_entry.entry_id, {})
        coordinator = entry_data.get("coordinator")
        dial_data = coordinator.data.get(self._selected_dial, {}) if coordinator else {}
        dial_name = dial_data.get("dial_name", f"Dial {self._selected_dial}")
        
        schema_dict = {
            vol.Optional(
                "bound_entity",
                default=current_config.get(CONF_BOUND_ENTITY) or "none",
            ): vol.In(entity_options),
            vol.Optional(
                "value_min",
                default=current_config.get(CONF_VALUE_MIN, 0),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Optional(
                "value_max",
                default=current_config.get(CONF_VALUE_MAX, 100),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Optional(
                "update_mode",
                default=current_config.get(CONF_UPDATE_MODE, "manual"),
            ): vol.In([UPDATE_MODE_AUTOMATIC, "manual"]),
            vol.Optional(
                "backlight_red",
                default=current_config.get(CONF_BACKLIGHT_COLOR, [100, 100, 100])[0],
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(
                "backlight_green",
                default=current_config.get(CONF_BACKLIGHT_COLOR, [100, 100, 100])[1],
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(
                "backlight_blue",
                default=current_config.get(CONF_BACKLIGHT_COLOR, [100, 100, 100])[2],
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        }

        return self.async_show_form(
            step_id="configure_dial",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"dial_name": dial_name},
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    client = VU1APIClient(
        host=data["host"],
        port=data["port"],
        api_key=data["api_key"],
    )

    try:
        _LOGGER.debug("Testing connection to VU1 server at %s:%s", data["host"], data["port"])
        
        # Test the connection by getting dial list
        dials = await client.get_dial_list()
        _LOGGER.debug("Successfully connected to VU1 server, found %d dials", len(dials))
        
    except VU1APIError as err:
        _LOGGER.error("VU1 API error during validation: %s", err)
        if "auth" in str(err).lower() or "key" in str(err).lower() or "forbidden" in str(err).lower():
            raise InvalidAuth from err
        raise CannotConnect from err
    finally:
        await client.close()

    # Return info that you want to store in the config entry.
    return {
        "title": f"VU1 Devices ({data['host']}:{data['port']})",
        "dial_count": len(dials),
    }