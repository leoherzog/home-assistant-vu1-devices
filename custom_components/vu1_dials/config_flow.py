"""Config flow for VU1 Devices integration."""
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector

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
        self._discovered_ingress: bool = False
        self._discovered_slug: Optional[str] = None
        self._supervisor_token: Optional[str] = None

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}
        description_placeholders: Dict[str, str] = {}

        # Try auto-discovery first
        if user_input is None:
            _LOGGER.info("VU1 integration auto-discovery starting...")
            discovered = await discover_vu1_server()
            _LOGGER.info("VU1 discovery result: %s", discovered)
            if discovered:
                # Store discovery information
                self._discovered_ingress = discovered.get("ingress", False)
                self._discovered_slug = discovered.get("slug")
                self._supervisor_token = discovered.get("supervisor_token")
                
                if self._discovered_ingress:
                    # For ingress, use actual IP and port
                    self._discovered_host = discovered.get("host", f"local-{self._discovered_slug}")
                    self._discovered_port = discovered.get("port", discovered.get("ingress_port", 5340))
                    unique_id = f"vu1_server_ingress_{self._discovered_slug}"
                else:
                    # Use actual discovered host/port or defaults
                    self._discovered_host = discovered.get("host", "localhost")
                    self._discovered_port = discovered.get("port", 5340)
                    unique_id = f"vu1_server_{self._discovered_host}_{self._discovered_port}"
                
                self._discovery_method = "addon" if discovered.get("addon_discovered") else "localhost"
                
                # Set unique ID to prevent duplicate configs
                await self.async_set_unique_id(unique_id)
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
            if self._discovered_ingress:
                # For ingress, store as host/port but with special markers
                full_input = {
                    "host": self._discovered_host,  # local-{slug}
                    "port": self._discovered_port,
                    "api_key": user_input["api_key"],
                    "ingress": True,
                    "ingress_slug": self._discovered_slug,
                    "supervisor_token": self._supervisor_token,
                }
            else:
                # For direct connection
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
                if self._discovered_ingress:
                    await self.async_set_unique_id(f"vu1_server_ingress_{self._discovered_slug}")
                else:
                    await self.async_set_unique_id(f"vu1_server_{full_input['host']}_{full_input['port']}")
                return self.async_create_entry(title=info["title"], data=full_input)

        # Show form with discovered info pre-filled
        if self._discovered_ingress:
            # For ingress, only show API key field
            discovery_schema = vol.Schema({
                vol.Required("api_key"): cv.string,
            })
        else:
            # For direct connection, show all fields
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
            if hasattr(self, '_discovered_ingress') and self._discovered_ingress:
                description_placeholders["discovery_info"] = f"Discovered VU1 Server add-on via Ingress proxy."
            else:
                description_placeholders["discovery_info"] = f"VU1 Server add-on auto-discovered at {self._discovered_host}:{self._discovered_port}. This uses the internal add-on network and doesn't require exposing ports."
        else:
            description_placeholders["discovery_info"] = f"VU1 Server discovered at {self._discovered_host}:{self._discovered_port}."

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
        self._dials = []
        self._selected_dial = None
        self._dial_config_data = {}

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        errors: Dict[str, str] = {}
        
        # Get available dials
        try:
            coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]
            if coordinator.data:
                dials_data = coordinator.data.get("dials", {})
                self._dials = [
                    {
                        "value": dial_uid, 
                        "label": f"{dial_data.get('dial_name', f'VU1 Dial {dial_uid}')} ({dial_uid})"
                    }
                    for dial_uid, dial_data in dials_data.items()
                ]
        except Exception as err:
            _LOGGER.warning("Could not get dial list for options: %s", err)
            self._dials = []

        if user_input is not None:
            if "configure_dial" in user_input and user_input["configure_dial"]:
                self._selected_dial = user_input["configure_dial"]
                return await self.async_step_configure_dial()
            
            # Just update interval
            return self.async_create_entry(title="", data=user_input)

        # Schema with dial selection and update interval
        schema_dict = {
            vol.Optional(
                "update_interval",
                default=self.config_entry.options.get("update_interval", 30),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
        }
        
        # Add dial configuration option if dials are available
        if self._dials:
            schema_dict[vol.Optional("configure_dial")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=self._dials)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "info": "Select a dial to configure sensor binding and advanced settings."
            },
        )

    async def async_step_configure_dial(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure specific dial with proper entity selector."""
        errors: Dict[str, str] = {}
        
        if not self._selected_dial:
            return await self.async_step_init()

        # Get current configuration
        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            current_config = config_manager.get_dial_config(self._selected_dial)
        except Exception as err:
            _LOGGER.error("Failed to get device config manager: %s", err)
            errors["base"] = "config_error"
            return await self.async_step_init()

        if user_input is not None:
            # Check if user just selected update mode - redirect to appropriate step
            if "update_mode" in user_input and len(user_input) == 1:
                # User just selected mode, show appropriate next step
                self._dial_config_data = {"update_mode": user_input["update_mode"]}
                if user_input["update_mode"] == "automatic":
                    return await self.async_step_configure_automatic()
                else:
                    return await self.async_step_configure_manual()
            
            try:
                # Process and save configuration
                processed_config = {
                    "update_mode": user_input.get("update_mode", "manual"),
                }
                
                # Only include bound entity and ranges for automatic mode
                if user_input.get("update_mode") == "automatic":
                    processed_config.update({
                        "bound_entity": user_input.get("bound_entity") or None,
                        "value_min": user_input.get("value_min", 0),
                        "value_max": user_input.get("value_max", 100),
                    })
                else:
                    # Clear bound entity for manual mode
                    processed_config.update({
                        "bound_entity": None,
                        "value_min": 0,
                        "value_max": 100,
                    })
                
                await config_manager.async_update_dial_config(self._selected_dial, processed_config)
                
                # Update sensor bindings
                from .sensor_binding import async_get_binding_manager
                binding_manager = async_get_binding_manager(self.hass)
                coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]
                if coordinator.data:
                    dials_data = coordinator.data.get("dials", {})
                    if self._selected_dial in dials_data:
                        await binding_manager._update_binding(
                            self._selected_dial, 
                            processed_config, 
                            dials_data[self._selected_dial]
                        )
                
                return self.async_create_entry(title="", data=self.config_entry.options)
                
            except Exception as err:
                _LOGGER.error("Failed to update dial configuration: %s", err)
                errors["base"] = "config_update_failed"

        # Get dial info for display
        coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]
        dials_data = coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._selected_dial, {})
        dial_name = dial_data.get("dial_name", self._selected_dial)

        # Simple mode selection schema
        schema = vol.Schema({
            vol.Required(
                "update_mode", 
                default=current_config.get("update_mode", "manual")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "automatic", "label": "Automatic (sensor-driven)"},
                        {"value": "manual", "label": "Manual only"}
                    ]
                )
            ),
        })

        return self.async_show_form(
            step_id="configure_dial",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "dial_name": dial_name,
                "info": "Choose how this dial should be updated. Manual mode requires using services or device controls. Automatic mode binds to a sensor for real-time updates."
            },
        )

    async def async_step_configure_automatic(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure automatic mode with sensor binding."""
        errors: Dict[str, str] = {}
        
        if not self._selected_dial:
            return await self.async_step_init()
        
        # Get current configuration
        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            current_config = config_manager.get_dial_config(self._selected_dial)
        except Exception as err:
            _LOGGER.error("Failed to get device config manager: %s", err)
            errors["base"] = "config_error"
            return await self.async_step_configure_dial()

        if user_input is not None:
            try:
                # Merge with mode from previous step
                processed_config = {
                    "update_mode": "automatic",
                    "bound_entity": user_input.get("bound_entity") or None,
                    "value_min": user_input.get("value_min", 0),
                    "value_max": user_input.get("value_max", 100),
                }
                
                await config_manager.async_update_dial_config(self._selected_dial, processed_config)
                
                # Update sensor bindings
                from .sensor_binding import async_get_binding_manager
                binding_manager = async_get_binding_manager(self.hass)
                coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]
                if coordinator.data:
                    dials_data = coordinator.data.get("dials", {})
                    if self._selected_dial in dials_data:
                        await binding_manager._update_binding(
                            self._selected_dial, 
                            processed_config, 
                            dials_data[self._selected_dial]
                        )
                
                return self.async_create_entry(title="", data=self.config_entry.options)
                
            except Exception as err:
                _LOGGER.error("Failed to update dial configuration: %s", err)
                errors["base"] = "config_update_failed"

        # Get dial info for display
        coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]
        dials_data = coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._selected_dial, {})
        dial_name = dial_data.get("dial_name", self._selected_dial)

        # Entity selector configuration
        entity_selector_config = selector.EntitySelectorConfig(
            domain=["sensor", "input_number", "number", "counter"],
            multiple=False,
        )

        # Automatic mode schema with sensor binding
        schema = vol.Schema({
            vol.Required(
                "bound_entity", 
                default=current_config.get("bound_entity")
            ): selector.EntitySelector(entity_selector_config),
            vol.Optional(
                "value_min", 
                default=current_config.get("value_min", 0)
            ): vol.All(vol.Coerce(float), vol.Range(min=-1000, max=1000)),
            vol.Optional(
                "value_max", 
                default=current_config.get("value_max", 100)
            ): vol.All(vol.Coerce(float), vol.Range(min=-1000, max=1000)),
        })

        return self.async_show_form(
            step_id="configure_automatic",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "dial_name": dial_name,
                "info": "Select a sensor to bind to this dial. The sensor's value will be mapped from the specified range to 0-100% on the dial."
            },
        )

    async def async_step_configure_manual(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure manual mode (just saves the mode)."""
        if not self._selected_dial:
            return await self.async_step_init()
            
        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            
            # Save manual mode configuration
            processed_config = {
                "update_mode": "manual",
                "bound_entity": None,
                "value_min": 0,
                "value_max": 100,
            }
            
            await config_manager.async_update_dial_config(self._selected_dial, processed_config)
            
            # Update sensor bindings (clears any existing binding)
            from .sensor_binding import async_get_binding_manager
            binding_manager = async_get_binding_manager(self.hass)
            coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]
            if coordinator.data:
                dials_data = coordinator.data.get("dials", {})
                if self._selected_dial in dials_data:
                    await binding_manager._update_binding(
                        self._selected_dial, 
                        processed_config, 
                        dials_data[self._selected_dial]
                    )
            
            return self.async_create_entry(title="", data=self.config_entry.options)
            
        except Exception as err:
            _LOGGER.error("Failed to update dial configuration: %s", err)
            return self.async_abort(reason="config_update_failed")


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


async def validate_input(hass: HomeAssistant, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    if data.get("ingress"):
        # Ingress connection via internal hostname
        client = VU1APIClient(
            host=data["host"],  # local-{slug}
            port=data["port"],
            ingress_slug=data["ingress_slug"],
            supervisor_token=data["supervisor_token"],
            api_key=data["api_key"],
        )
        connection_info = f"ingress ({data['ingress_slug']})"
    else:
        # Direct connection
        client = VU1APIClient(
            host=data["host"],
            port=data["port"],
            api_key=data["api_key"],
        )
        connection_info = f"{data['host']}:{data['port']}"

    try:
        _LOGGER.debug("Testing connection to VU1 server at %s", connection_info)
        
        # Use enhanced API key testing for better error information
        api_test_result = await client.test_api_key()
        if not api_test_result["valid"]:
            _LOGGER.error("API key validation failed: %s", api_test_result.get("error", "Unknown error"))
            raise InvalidAuth(f"Invalid API Key: {api_test_result.get('error', 'Unknown error')}")
        
        dials = api_test_result.get("dials", [])
        _LOGGER.debug("Successfully connected to VU1 server, found %d dials", len(dials))
        
    except InvalidAuth:
        raise  # Re-raise InvalidAuth exceptions
    except VU1APIError as err:
        _LOGGER.error("VU1 API error during validation: %s", err)
        if "auth" in str(err).lower() or "key" in str(err).lower() or "forbidden" in str(err).lower():
            raise InvalidAuth from err
        raise CannotConnect from err
    finally:
        await client.close()

    # Return info that you want to store in the config entry.
    return {
        "title": f"VU1 Devices ({connection_info})",
        "dial_count": len(dials),
    }