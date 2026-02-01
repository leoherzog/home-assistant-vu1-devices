"""Config flow for VU1 Dials integration."""
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector

from .const import DOMAIN
from .vu1_api import VU1APIClient, VU1APIError, discover_vu1_addon

_LOGGER = logging.getLogger(__name__)

__all__ = ["ConfigFlow", "OptionsFlowHandler"]


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VU1 Dials."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._discovered_host: str | None = None
        self._discovered_port: int | None = None
        self._discovery_method: str | None = None
        self._discovered_ingress: bool = False
        self._discovered_slug: str | None = None
        self._supervisor_token: str | None = None
        self._addon_available: bool = False
        self._addon_name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - show connection type selection."""
        errors: dict[str, str] = {}

        if user_input is None:
            # First, check if VU1 Server add-on is available via Supervisor API
            _LOGGER.info("Checking for VU1 Server add-on...")
            discovered = await discover_vu1_addon()
            
            if discovered and discovered.get("addon_discovered"):
                self._addon_available = True
                self._discovered_ingress = discovered.get("ingress", False)
                self._discovered_slug = discovered.get("slug")
                self._supervisor_token = discovered.get("supervisor_token")
                self._discovered_host = discovered.get("host", discovered.get("addon_ip"))
                self._discovered_port = discovered.get("port", discovered.get("ingress_port", 5340))
                
                # Extract user-friendly name from slug for display
                addon_slug = discovered.get("slug", "")
                self._addon_name = addon_slug.split("_")[-1] if "_" in addon_slug else addon_slug
                
                _LOGGER.info("VU1 Server add-on found: %s", self._addon_name)
            else:
                _LOGGER.info("No VU1 Server add-on found")

            # Build connection type options (add-on first if available)
            options = [
                {"value": "manual", "label": "Manual configuration"}
            ]
            
            if self._addon_available:
                options.insert(0, {"value": "addon", "label": f"VU1 Server Add-on ({self._addon_name or 'Unknown'})"})
            
            schema = vol.Schema({
                vol.Required("connection_type"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                )
            })
            
            return self.async_show_form(
                step_id="user",
                data_schema=schema,
                errors=errors,
                description_placeholders={
                    "info": "Select how to connect to your VU1 Server."
                }
            )

        if user_input.get("connection_type") == "addon":
            return await self.async_step_addon()
        else:
            return await self.async_step_manual()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Set unique ID to prevent duplicate manual configurations
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

        schema = vol.Schema({
            vol.Required("host", default="localhost"): cv.string,
            vol.Required("port", default=5340): cv.port,
            vol.Required("api_key"): cv.string,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": "Enter the connection details for your VU1 Server."
            }
        )

    async def async_step_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle add-on configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Build configuration using discovered add-on details
            full_input = {
                "host": self._discovered_host,
                "port": self._discovered_port,
                "api_key": user_input["api_key"],
            }
            
            # Add ingress configuration if applicable
            if self._discovered_ingress:
                full_input.update({
                    "ingress": True,
                    "ingress_slug": self._discovered_slug,
                    "supervisor_token": self._supervisor_token,
                })
            
            # Set unique ID based on connection type
            if self._discovered_ingress:
                await self.async_set_unique_id(f"vu1_server_ingress_{self._discovered_slug}")
            else:
                await self.async_set_unique_id(f"vu1_server_{self._discovered_host}_{self._discovered_port}")
            self._abort_if_unique_id_configured()
            
            try:
                info = await validate_input(self.hass, full_input)
                # Override title to show it's an add-on
                info["title"] = f"Add-on ({self._addon_name or 'Unknown'})"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=full_input)

        schema = vol.Schema({
            vol.Required("api_key"): cv.string,
        })

        return self.async_show_form(
            step_id="addon",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": f"Enter the API key for the VU1 Server Add-on ({self._addon_name or 'Unknown'})."
            }
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            # Build updated configuration
            updated_data = dict(entry.data)

            # Only update fields that were provided
            if "host" in user_input:
                updated_data["host"] = user_input["host"]
            if "port" in user_input:
                updated_data["port"] = user_input["port"]
            if "api_key" in user_input:
                updated_data["api_key"] = user_input["api_key"]

            try:
                await validate_input(self.hass, updated_data)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=updated_data,
                )

        # Determine current values for defaults
        is_ingress = entry.data.get("ingress", False)

        if is_ingress:
            # For ingress connections, only allow API key change
            schema = vol.Schema({
                vol.Required("api_key", default=entry.data.get("api_key", "")): cv.string,
            })
        else:
            # For direct connections, allow host/port/api_key changes
            schema = vol.Schema({
                vol.Required("host", default=entry.data.get("host", "localhost")): cv.string,
                vol.Required("port", default=entry.data.get("port", 5340)): cv.port,
                vol.Required("api_key", default=entry.data.get("api_key", "")): cv.string,
            })

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": "Update connection settings for your VU1 Server."
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._dials: list[dict[str, str]] = []
        self._selected_dial: str | None = None
        self._dial_config_data: dict[str, Any] = {}
        # Store options collected during the flow to preserve update_interval
        self._collected_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        
        try:
            coordinator = self.config_entry.runtime_data.coordinator
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
            # Preserve update_interval in collected options for later
            if "update_interval" in user_input:
                self._collected_options["update_interval"] = user_input["update_interval"]

            if "configure_dial" in user_input and user_input["configure_dial"]:
                self._selected_dial = user_input["configure_dial"]
                return await self.async_step_configure_dial()

            # Merge collected options with user input for final entry
            final_options = {**self.config_entry.options, **self._collected_options, **user_input}
            return self.async_create_entry(title="", data=final_options)

        schema_dict = {
            vol.Optional(
                "update_interval",
                default=self.config_entry.options.get("update_interval", 30),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
        }
        
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
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure specific dial - choose what to configure."""
        if not self._selected_dial:
            return await self.async_step_init()

        if user_input is not None:
            action = user_input["dial_action"]
            if action == "update_mode":
                return await self.async_step_configure_update_mode()
            elif action == "upload_image":
                return await self.async_step_upload_image()

        coordinator = self.config_entry.runtime_data.coordinator
        dials_data = coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._selected_dial, {})
        dial_name = dial_data.get("dial_name", self._selected_dial)

        schema = vol.Schema({
            vol.Required("dial_action"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": "update_mode", "label": "Configure update mode"},
                        {"value": "upload_image", "label": "Upload background image"},
                    ]
                )
            ),
        })

        return self.async_show_form(
            step_id="configure_dial",
            data_schema=schema,
            description_placeholders={
                "dial_name": dial_name,
                "info": "Choose what to configure for this dial."
            },
        )

    async def async_step_configure_update_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose update mode for the dial."""
        errors: dict[str, str] = {}

        if not self._selected_dial:
            return await self.async_step_init()

        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            current_config = config_manager.get_dial_config(self._selected_dial)
        except Exception as err:
            _LOGGER.error("Failed to get device config manager: %s", err)
            return self.async_abort(reason="config_error")

        if user_input is not None:
            if user_input["update_mode"] == "automatic":
                return await self.async_step_configure_automatic()
            else:
                return await self.async_step_configure_manual()

        coordinator = self.config_entry.runtime_data.coordinator
        dials_data = coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._selected_dial, {})
        dial_name = dial_data.get("dial_name", self._selected_dial)

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
            step_id="configure_update_mode",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "dial_name": dial_name,
                "info": "Choose how this dial should be updated."
            },
        )

    async def async_step_upload_image(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload a background image for the dial."""
        errors: dict[str, str] = {}

        if not self._selected_dial:
            return await self.async_step_init()

        if user_input is not None:
            uploaded_file_id = user_input.get("background_image")
            if uploaded_file_id:
                try:
                    from homeassistant.components.file_upload import process_uploaded_file
                    import mimetypes

                    with process_uploaded_file(self.hass, uploaded_file_id) as file_path:
                        image_data = await self.hass.async_add_executor_job(file_path.read_bytes)
                        content_type = mimetypes.guess_type(str(file_path))[0] or "image/png"

                    client = self.config_entry.runtime_data.client
                    await client.set_dial_image(self._selected_dial, image_data, content_type)

                    coordinator = self.config_entry.runtime_data.coordinator
                    await coordinator.async_request_refresh()
                except Exception as err:
                    _LOGGER.error("Failed to upload image for dial %s: %s", self._selected_dial, err)
                    errors["base"] = "image_upload_failed"

            if not errors:
                final_options = {**self.config_entry.options, **self._collected_options}
                return self.async_create_entry(title="", data=final_options)

        from homeassistant.helpers.selector import FileSelector, FileSelectorConfig

        schema = vol.Schema({
            vol.Required("background_image"): FileSelector(
                FileSelectorConfig(accept="image/png,image/jpeg,.png,.jpg,.jpeg")
            ),
        })

        coordinator = self.config_entry.runtime_data.coordinator
        dials_data = coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._selected_dial, {})
        dial_name = dial_data.get("dial_name", self._selected_dial)

        return self.async_show_form(
            step_id="upload_image",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "dial_name": dial_name,
                "info": "Upload a PNG or JPEG image. The VU1 dial display is 144x200 pixels.",
            },
        )

    async def async_step_configure_automatic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure automatic mode with sensor binding."""
        errors: dict[str, str] = {}
        
        if not self._selected_dial:
            return await self.async_step_init()
        
        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            current_config = config_manager.get_dial_config(self._selected_dial)
        except Exception as err:
            _LOGGER.error("Failed to get device config manager: %s", err)
            return self.async_abort(reason="config_error")

        if user_input is not None:
            value_min = user_input.get("value_min", 0)
            value_max = user_input.get("value_max", 100)
            if value_min >= value_max:
                errors["base"] = "value_min_greater_than_max"
            else:
                try:
                    processed_config = {
                        "update_mode": "automatic",
                        "bound_entity": user_input.get("bound_entity") or None,
                        "value_min": value_min,
                        "value_max": value_max,
                    }
                    
                    await config_manager.async_update_dial_config(self._selected_dial, processed_config)
                    
                    from .sensor_binding import async_get_binding_manager
                    binding_manager = async_get_binding_manager(self.hass)
                    if binding_manager:
                        await binding_manager.async_reconfigure_dial_binding(self._selected_dial)

                    # Merge collected options (including update_interval) with existing options
                    final_options = {**self.config_entry.options, **self._collected_options}
                    return self.async_create_entry(title="", data=final_options)

                except Exception as err:
                    _LOGGER.error("Failed to update dial configuration: %s", err)
                    errors["base"] = "config_update_failed"

        coordinator = self.config_entry.runtime_data.coordinator
        dials_data = coordinator.data.get("dials", {})
        dial_data = dials_data.get(self._selected_dial, {})
        dial_name = dial_data.get("dial_name", self._selected_dial)

        entity_selector_config = selector.EntitySelectorConfig(
            domain=["sensor", "input_number", "number", "counter"],
            multiple=False,
        )

        schema = vol.Schema({
            vol.Required(
                "bound_entity", 
                default=current_config.get("bound_entity")
            ): selector.EntitySelector(entity_selector_config),
            vol.Optional(
                "value_min", 
                default=current_config.get("value_min", 0)
            ): vol.Coerce(float),
            vol.Optional(
                "value_max", 
                default=current_config.get("value_max", 100)
            ): vol.Coerce(float),
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
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure manual mode (just saves the mode)."""
        if not self._selected_dial:
            return await self.async_step_init()
            
        try:
            from .device_config import async_get_config_manager
            config_manager = async_get_config_manager(self.hass)
            
            processed_config = {
                "update_mode": "manual",
                "bound_entity": None,
                "value_min": 0,
                "value_max": 100,
            }
            
            await config_manager.async_update_dial_config(self._selected_dial, processed_config)
            
            from .sensor_binding import async_get_binding_manager
            binding_manager = async_get_binding_manager(self.hass)
            if binding_manager:
                await binding_manager.async_reconfigure_dial_binding(self._selected_dial)

            # Merge collected options (including update_interval) with existing options
            final_options = {**self.config_entry.options, **self._collected_options}
            return self.async_create_entry(title="", data=final_options)

        except Exception as err:
            _LOGGER.error("Failed to update dial configuration: %s", err)
            return self.async_abort(reason="config_update_failed")


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    if data.get("ingress"):
        client = VU1APIClient(
            host=data["host"],
            port=data["port"],
            ingress_slug=data["ingress_slug"],
            supervisor_token=data["supervisor_token"],
            api_key=data["api_key"],
        )
        connection_info = f"ingress ({data['ingress_slug']})"
    else:
        client = VU1APIClient(
            host=data["host"],
            port=data["port"],
            api_key=data["api_key"],
        )
        connection_info = f"{data['host']}:{data['port']}"

    try:
        _LOGGER.debug("Testing connection to VU1 server at %s", connection_info)
        
        connection_result = await client.test_connection()
        if not connection_result["connected"]:
            _LOGGER.error("Connection failed: %s", connection_result.get("error", "Unknown error"))
            raise CannotConnect(f"Cannot connect to VU1 server: {connection_result.get('error', 'Unknown error')}")
        
        if not connection_result["authenticated"]:
            _LOGGER.error("API key validation failed: %s", connection_result.get("error", "Unknown error"))
            raise InvalidAuth(f"Invalid API Key: {connection_result.get('error', 'Unknown error')}")
        
        dials = connection_result.get("dials", [])
        _LOGGER.debug("Successfully connected to VU1 server, found %d dials", len(dials))
        
    except InvalidAuth:
        raise
    except VU1APIError as err:
        _LOGGER.error("VU1 API error during validation: %s", err)
        if "auth" in str(err).lower() or "key" in str(err).lower() or "forbidden" in str(err).lower():
            raise InvalidAuth from err
        raise CannotConnect from err
    finally:
        await client.close()

    return {
        "title": f"VU1 Server ({connection_info})",
        "dial_count": len(dials),
    }