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
                description_placeholders["discovery_info"] = f"VU1 Server add-on auto-discovered with ingress enabled. This uses the internal Home Assistant proxy and doesn't require exposing ports."
            else:
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
        self._dials: list = []
        self._selected_dial: Optional[str] = None

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        errors: Dict[str, str] = {}
        
        # Get dial list for configuration
        try:
            domain_data = self.hass.data.get(DOMAIN, {})
            for entry_data in domain_data.values():
                if "coordinator" in entry_data:
                    coordinator = entry_data["coordinator"]
                    if coordinator.data:
                        self._dials = list(coordinator.data.values())
                        break
        except Exception as err:
            _LOGGER.warning("Could not get dial list for options: %s", err)
            self._dials = []

        if user_input is not None:
            if "configure_dial" in user_input and user_input["configure_dial"]:
                self._selected_dial = user_input["configure_dial"]
                return await self.async_step_configure_dial()
            
            # Just update interval, no dial configuration
            return self.async_create_entry(title="", data=user_input)

        # Build schema with dial selection if dials are available
        schema_dict = {
            vol.Optional(
                "update_interval",
                default=self.config_entry.options.get("update_interval", 30),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
        }
        
        # Add dial configuration option if dials are available
        if self._dials:
            dial_options = {dial["uid"]: f"{dial.get('name', dial['uid'])} ({dial['uid']})" for dial in self._dials}
            schema_dict[vol.Optional("configure_dial")] = vol.In(dial_options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )
    
    async def async_step_configure_dial(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Configure a specific dial."""
        errors: Dict[str, str] = {}
        
        if not self._selected_dial:
            return await self.async_step_init()
        
        # Get device configuration manager
        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            current_config = config_manager.get_dial_config(self._selected_dial)
        except Exception as err:
            _LOGGER.error("Failed to get device config manager: %s", err)
            errors["base"] = "config_error"
            return await self.async_step_init()
        
        if user_input is not None:
            try:
                # Process user input to match expected format
                processed_config = {
                    "bound_entity": user_input.get("bound_entity") or None,
                    "value_min": user_input.get("value_min", 0),
                    "value_max": user_input.get("value_max", 100),
                    "update_mode": user_input.get("update_mode", "manual"),
                    "backlight_color": [
                        user_input.get("backlight_red", 100),
                        user_input.get("backlight_green", 100),
                        user_input.get("backlight_blue", 100)
                    ],
                    "dial_easing": user_input.get("dial_easing", "linear"),
                    "backlight_easing": user_input.get("backlight_easing", "linear"),
                }
                
                # Update dial configuration
                await config_manager.async_update_dial_config(self._selected_dial, processed_config)
                
                # Update sensor bindings if needed
                from .sensor_binding import async_get_binding_manager
                binding_manager = async_get_binding_manager(self.hass)
                # Force refresh the binding for this dial
                domain_data = self.hass.data.get(DOMAIN, {})
                for entry_data in domain_data.values():
                    if "coordinator" in entry_data:
                        coordinator = entry_data["coordinator"]
                        if coordinator.data and self._selected_dial in coordinator.data:
                            await binding_manager._update_binding(
                                self._selected_dial, 
                                processed_config, 
                                coordinator.data[self._selected_dial]
                            )
                            break
                
                return self.async_create_entry(title="", data=self.config_entry.options)
                
            except Exception as err:
                _LOGGER.error("Failed to update dial configuration: %s", err)
                errors["base"] = "config_update_failed"
        
        # Get available sensor entities for binding
        entity_options = self._get_sensor_entities()
        
        # Get dial info for display
        dial_info = next((dial for dial in self._dials if dial["uid"] == self._selected_dial), {})
        dial_name = dial_info.get("name", self._selected_dial)
        
        # Build configuration schema
        schema = vol.Schema({
            vol.Optional(
                "bound_entity", 
                default=current_config.get("bound_entity")
            ): vol.In(entity_options) if entity_options else cv.string,
            vol.Optional(
                "value_min", 
                default=current_config.get("value_min", 0)
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
            vol.Optional(
                "value_max", 
                default=current_config.get("value_max", 100)
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
            vol.Optional(
                "update_mode", 
                default=current_config.get("update_mode", "manual")
            ): vol.In({"automatic": "Automatic (sensor-driven)", "manual": "Manual only"}),
            vol.Optional(
                "backlight_red", 
                default=current_config.get("backlight_color", [100, 100, 100])[0]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(
                "backlight_green", 
                default=current_config.get("backlight_color", [100, 100, 100])[1]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(
                "backlight_blue", 
                default=current_config.get("backlight_color", [100, 100, 100])[2]
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(
                "dial_easing", 
                default=current_config.get("dial_easing", "linear")
            ): vol.In({"linear": "Linear", "ease-in": "Ease In", "ease-out": "Ease Out", "ease-in-out": "Ease In-Out"}),
            vol.Optional(
                "backlight_easing", 
                default=current_config.get("backlight_easing", "linear")
            ): vol.In({"linear": "Linear", "ease-in": "Ease In", "ease-out": "Ease Out", "ease-in-out": "Ease In-Out"}),
        })
        
        return self.async_show_form(
            step_id="configure_dial",
            data_schema=schema,
            errors=errors,
            description_placeholders={"dial_name": dial_name},
        )
    
    def _get_sensor_entities(self) -> Dict[str, str]:
        """Get available sensor entities for binding."""
        import homeassistant.helpers.entity_registry as er
        
        entity_registry = er.async_get(self.hass)
        entities = {}
        
        # Add "None" option for no binding
        entities[""] = "None (no binding)"
        
        # Get entities from relevant domains
        for entity in entity_registry.entities.values():
            if entity.domain in ["sensor", "input_number", "number", "counter"]:
                # Get state to show current value if available
                state = self.hass.states.get(entity.entity_id)
                display_name = entity.name or entity.entity_id
                
                if state and state.state not in ["unknown", "unavailable"]:
                    try:
                        float(state.state)
                        display_name += f" (current: {state.state})"
                    except (ValueError, TypeError):
                        pass
                
                entities[entity.entity_id] = display_name
        
        return entities


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