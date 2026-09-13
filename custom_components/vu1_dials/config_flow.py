"""Config flow for VU1 Dials integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .device_config import async_get_config_manager
from .sensor_binding import async_get_binding_manager
from .vu1_api import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    VU1APIClient,
    VU1APIError,
    VU1AuthError,
    discover_vu1_addon,
)

_LOGGER = logging.getLogger(__name__)

__all__ = ["ConfigFlow", "OptionsFlowHandler"]

API_KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD, autocomplete="current-password"
    )
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VU1 Dials."""

    def __init__(self) -> None:
        """Initialize config flow."""
        self._addon: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the VU1 Server add-on when installed, else go to manual setup."""
        self._addon = await discover_vu1_addon(async_get_clientsession(self.hass))
        if not self._addon:
            return await self.async_step_manual()
        return self.async_show_menu(step_id="user", menu_options=["addon", "manual"])

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await validate_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=f"VU1 Server ({user_input[CONF_HOST]}:{user_input[CONF_PORT]})",
                    data=user_input,
                )

        schema = vol.Schema({
            vol.Required(CONF_HOST, default="localhost"): cv.string,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
            vol.Required(CONF_API_KEY): API_KEY_SELECTOR,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_addon(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle add-on configuration."""
        if not self._addon["running"]:
            return self.async_abort(reason="addon_not_running")

        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                CONF_HOST: self._addon["host"],
                CONF_PORT: self._addon["port"],
                CONF_API_KEY: user_input[CONF_API_KEY],
            }
            errors = await validate_input(self.hass, data)
            if not errors:
                return self.async_create_entry(title="VU1 Server (Add-on)", data=data)

        return self.async_show_form(
            step_id="addon",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        host, port = entry.data[CONF_HOST], entry.data[CONF_PORT]

        if user_input is not None:
            updated_data = {**entry.data, **{k: v for k, v in user_input.items() if v}}
            errors = await validate_input(self.hass, updated_data)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates=updated_data,
                )
            host, port = user_input[CONF_HOST], user_input[CONF_PORT]

        schema = vol.Schema({
            vol.Required(CONF_HOST, default=host): cv.string,
            vol.Required(CONF_PORT, default=port): cv.port,
            vol.Optional(CONF_API_KEY): API_KEY_SELECTOR,
        })

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the API key is rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication by collecting a new API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            errors = await validate_input(self.hass, {**entry.data, **user_input})
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle options flow.

    Subclasses OptionsFlowWithReload so saved options (update_interval, timeout)
    take effect immediately — the base class reloads the entry on save, which
    re-creates the coordinator/client with the new values.
    """

    def __init__(self) -> None:
        """Initialize options flow."""
        self._dials: list[dict[str, str]] = []
        self._selected_dial: str | None = None
        self._collected_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        try:
            coordinator = self.config_entry.runtime_data.coordinator
            if coordinator.data:
                dials_data = coordinator.data.get("dials", {})
                device_registry = dr.async_get(self.hass)
                self._dials = []
                for dial_uid, dial_data in dials_data.items():
                    # Prefer device registry name (respects name_by_user) over server name
                    device = device_registry.async_get_device_by_identifier((DOMAIN, dial_uid), self.config_entry.entry_id)
                    if device:
                        dial_name = device.name_by_user or device.name or dial_data.get("dial_name", f"VU1 Dial {dial_uid}")
                    else:
                        dial_name = dial_data.get("dial_name", f"VU1 Dial {dial_uid}")
                    self._dials.append({
                        "value": dial_uid,
                        "label": f"{dial_name} ({dial_uid})",
                    })
        except AttributeError as err:
            _LOGGER.warning("Could not get dial list for options: %s", err)
            self._dials = []

        if user_input is not None:
            self._collected_options = {k: user_input[k] for k in ("update_interval", "timeout")}

            if user_input.get("configure_dial"):
                self._selected_dial = user_input["configure_dial"]
                return await self.async_step_configure_dial()

            return self._async_finish()

        schema_dict = {
            vol.Optional(
                "update_interval",
                default=self.config_entry.options.get(
                    "update_interval", DEFAULT_UPDATE_INTERVAL
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            vol.Optional(
                "timeout",
                default=self.config_entry.options.get("timeout", DEFAULT_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        }

        if self._dials:
            schema_dict[vol.Optional("configure_dial")] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=self._dials)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )

    @callback
    def _async_finish(self) -> ConfigFlowResult:
        """Save the existing options merged with those collected in this flow."""
        return self.async_create_entry(
            data={**self.config_entry.options, **self._collected_options}
        )

    def _dial_placeholders(self) -> dict[str, str]:
        """Return the selected dial's name for step descriptions."""
        dial = self.config_entry.runtime_data.coordinator.data["dials"].get(
            self._selected_dial, {}
        )
        return {"dial_name": dial.get("dial_name", self._selected_dial)}

    async def async_step_configure_dial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure specific dial - choose what to configure."""
        return self.async_show_menu(
            step_id="configure_dial",
            menu_options=["configure_update_mode", "upload_image"],
            description_placeholders=self._dial_placeholders(),
        )

    async def async_step_configure_update_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose update mode for the dial."""
        if user_input is not None:
            if user_input["update_mode"] == "automatic":
                return await self.async_step_configure_automatic()
            return await self.async_step_configure_manual()

        current_config = async_get_config_manager(self.hass).get_dial_config(
            self._selected_dial
        )
        schema = vol.Schema({
            vol.Required(
                "update_mode",
                default=current_config.get("update_mode", "manual")
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["automatic", "manual"], translation_key="update_mode"
                )
            ),
        })

        return self.async_show_form(
            step_id="configure_update_mode",
            data_schema=schema,
            description_placeholders=self._dial_placeholders(),
        )

    async def async_step_upload_image(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Upload a background image for the dial."""
        errors: dict[str, str] = {}

        if user_input is not None:

            def _read_upload(file_id: str) -> bytes:
                with process_uploaded_file(self.hass, file_id) as file_path:
                    return file_path.read_bytes()

            try:
                image_data = await self.hass.async_add_executor_job(
                    _read_upload, user_input["background_image"]
                )
                await self.config_entry.runtime_data.client.set_dial_image(
                    self._selected_dial, image_data
                )
            except (HomeAssistantError, OSError, ValueError) as err:
                _LOGGER.error("Failed to upload image for dial %s: %s", self._selected_dial, err)
                errors["base"] = "image_upload_failed"
            else:
                await self.config_entry.runtime_data.coordinator.async_request_refresh()
                return self._async_finish()

        schema = vol.Schema({
            vol.Required("background_image"): selector.FileSelector(
                selector.FileSelectorConfig(accept="image/png,image/jpeg,.png,.jpg,.jpeg")
            ),
        })

        return self.async_show_form(
            step_id="upload_image",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._dial_placeholders(),
        )

    async def async_step_configure_automatic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure automatic mode with sensor binding."""
        errors: dict[str, str] = {}
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(self._selected_dial)

        if user_input is not None:
            value_min = user_input.get("value_min", 0)
            value_max = user_input.get("value_max", 100)
            if value_min >= value_max:
                errors["base"] = "value_min_greater_than_max"
            else:
                await config_manager.async_update_dial_config(
                    self._selected_dial,
                    {
                        "update_mode": "automatic",
                        "bound_entity": user_input.get("bound_entity") or None,
                        "value_min": value_min,
                        "value_max": value_max,
                    },
                )
                await async_get_binding_manager(
                    self.hass
                ).async_reconfigure_dial_binding(self._selected_dial)
                return self._async_finish()

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
            description_placeholders=self._dial_placeholders(),
        )

    async def async_step_configure_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure manual mode (just saves the mode)."""
        await async_get_config_manager(self.hass).async_update_dial_config(
            self._selected_dial, {"update_mode": "manual"}
        )
        await async_get_binding_manager(self.hass).async_reconfigure_dial_binding(
            self._selected_dial
        )
        return self._async_finish()


async def validate_input(hass: HomeAssistant, data: Mapping[str, Any]) -> dict[str, str]:
    """Return form errors for the connection details, empty when they work."""
    client = VU1APIClient(
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        api_key=data[CONF_API_KEY],
        session=async_get_clientsession(hass),
    )
    try:
        await client.get_dial_list()
    except VU1AuthError:
        return {"base": "invalid_auth"}
    except VU1APIError:
        return {"base": "cannot_connect"}
    except Exception:  # pylint: disable=broad-except
        _LOGGER.exception("Unexpected exception")
        return {"base": "unknown"}
    return {}
