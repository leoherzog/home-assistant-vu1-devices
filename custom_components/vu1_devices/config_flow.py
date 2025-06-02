"""Config flow for VU1 Devices integration."""
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .vu1_api import VU1APIClient, VU1APIError, discover_vu1_server

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
        description_placeholders: Dict[str, str] = {}

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

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "update_interval",
                        default=self.config_entry.options.get("update_interval", 30),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                }
            ),
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