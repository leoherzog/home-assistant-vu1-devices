"""Support for VU1 dial behavior preset select entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BEHAVIOR_PRESETS, async_setup_dial_entities
from .config_entities import VU1ConfigEntityBase
from .device_config import async_get_config_manager

if TYPE_CHECKING:
    from . import VU1ConfigEntry

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up VU1 select entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str, dial_info: dict[str, Any]) -> list:
        return [VU1BehaviorSelect(coordinator, dial_uid, dial_info)]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1BehaviorSelect(VU1ConfigEntityBase, SelectEntity):
    """Select entity for dial behavior presets."""

    def __init__(self, coordinator, dial_uid: str, dial_data: dict[str, Any]) -> None:
        """Initialize the behavior select entity."""
        # VU1ConfigEntityBase provides the config-change listener lifecycle,
        # device_info, availability, and the CONFIG entity category.
        super().__init__(coordinator, dial_uid, dial_data)
        self._attr_unique_id = f"{dial_uid}_behavior_preset"
        self._attr_translation_key = "behavior_preset"
        # Option values are the human-readable preset names ("Responsive",
        # "Balanced", ...) and double as the reported entity state. They are
        # intentionally NOT migrated to snake_case slugs: doing so would change
        # the entity's state value (a behavior change). HA only applies
        # entity.select.<key>.state.<option> translations when the option
        # values are snake_case, so the option labels remain as-is here.
        self._attr_options = [preset["name"] for preset in BEHAVIOR_PRESETS.values()]

    @property
    def current_option(self) -> str:
        """Return the currently selected option."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)

        # Check if current values match any preset
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_key == "custom":
                continue

            # Convert both config and preset values to int for comparison
            # to handle cases where config values might be stored as strings
            try:
                config_dial_period = int(config.get("dial_easing_period", 50))
                config_dial_step = int(config.get("dial_easing_step", 5))
                config_backlight_period = int(config.get("backlight_easing_period", 50))
                config_backlight_step = int(config.get("backlight_easing_step", 5))

                if (config_dial_period == preset_data["dial_easing_period"] and
                    config_dial_step == preset_data["dial_easing_step"] and
                    config_backlight_period == preset_data["backlight_easing_period"] and
                    config_backlight_step == preset_data["backlight_easing_step"]):
                    return preset_data["name"]
            except (ValueError, TypeError):
                # If any conversion fails, skip this preset comparison
                continue

        return "Custom"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Find the preset configuration
        preset_config = None
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_data["name"] == option:
                preset_config = preset_data
                break

        if not preset_config or option == "Custom":
            # Custom option selected, don't change values
            return

        # Build the new config but do NOT persist it yet.
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(self._dial_uid)
        config_updates = {
            **current_config,
            "dial_easing_period": preset_config["dial_easing_period"],
            "dial_easing_step": preset_config["dial_easing_step"],
            "backlight_easing_period": preset_config["backlight_easing_period"],
            "backlight_easing_step": preset_config["backlight_easing_step"],
        }

        # Apply to hardware first; only persist the preset if the dial accepts
        # it, so a failure doesn't leave the UI showing a preset the hardware
        # never received. _apply_easing_config raises HomeAssistantError on
        # failure (config is left untouched, so current_option is unchanged).
        await self._apply_easing_config(preset_config)
        await config_manager.async_update_dial_config(self._dial_uid, config_updates)

        # Update sensor bindings if needed
        from .sensor_binding import async_get_binding_manager
        binding_manager = async_get_binding_manager(self.hass)
        if binding_manager:
            await binding_manager.async_reconfigure_dial_binding(self._dial_uid)

        # Update UI state immediately — current_option reads from config manager
        # which was already updated above. Don't use async_request_refresh() here
        # because the VU1 server queues commands asynchronously and an immediate
        # poll would return stale easing values.
        self.async_write_ha_state()

        _LOGGER.info("Applied %s behavior preset to dial %s", option, self._dial_uid)

    async def _apply_easing_config(self, preset_config: dict[str, Any]) -> None:
        """Apply easing configuration to the server, raising on failure."""
        from . import _get_dial_client_and_coordinator
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        if not result:
            raise HomeAssistantError(
                f"Cannot communicate with dial {self._dial_uid}"
            )

        client, coordinator = result
        # Mark grace period to prevent sync loops
        coordinator.mark_behavior_change_from_ha(self._dial_uid)
        try:
            # Apply dial easing
            await client.set_dial_easing(
                self._dial_uid,
                preset_config["dial_easing_period"],
                preset_config["dial_easing_step"],
            )
            # Apply backlight easing
            await client.set_backlight_easing(
                self._dial_uid,
                preset_config["backlight_easing_period"],
                preset_config["backlight_easing_step"],
            )
        except Exception as err:
            _LOGGER.error("Failed to apply behavior preset for %s: %s", self._dial_uid, err)
            raise HomeAssistantError(f"Failed to apply behavior preset: {err}")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)

        # Find current preset or custom
        current = self.current_option
        description = "Custom configuration"
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_data["name"] == current:
                description = preset_data["description"]
                break

        return {
            "description": description,
            "dial_easing_period": config.get("dial_easing_period", 50),
            "dial_easing_step": config.get("dial_easing_step", 5),
            "backlight_easing_period": config.get("backlight_easing_period", 50),
            "backlight_easing_step": config.get("backlight_easing_step", 5),
        }
