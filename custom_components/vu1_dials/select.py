"""Support for VU1 dial behavior preset select entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
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
        return [VU1BehaviorSelect(coordinator, dial_uid)]

    async_setup_dial_entities(
        coordinator, config_entry, async_add_entities, entity_factory,
    )


class VU1BehaviorSelect(VU1ConfigEntityBase, SelectEntity):
    """Select entity for dial behavior presets."""

    def __init__(self, coordinator, dial_uid: str) -> None:
        """Initialize the behavior select entity."""
        # VU1ConfigEntityBase provides the config-change listener lifecycle,
        # device_info, availability, and the CONFIG entity category.
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_behavior_preset"
        self._attr_translation_key = "behavior_preset"
        # Option values are the snake_case preset slugs ("responsive",
        # "balanced", ...) so HA applies the entity.select.<key>.state.<option>
        # translations for the reported state.
        self._attr_options = list(BEHAVIOR_PRESETS.keys())

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
                    return preset_key
            except (ValueError, TypeError):
                # If any conversion fails, skip this preset comparison
                continue

        return "custom"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        # Accept the snake_case slug; also map legacy display names
        # ("Responsive") so automations from before the slug migration resolve.
        preset_key = option
        if preset_key not in BEHAVIOR_PRESETS:
            preset_key = next(
                (key for key, data in BEHAVIOR_PRESETS.items() if data["name"] == option),
                option,
            )

        preset_config = BEHAVIOR_PRESETS.get(preset_key)
        if not preset_config or preset_key == "custom":
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
        # never received. _apply_easing_config_to_server raises
        # HomeAssistantError on failure (config is left untouched, so
        # current_option is unchanged).
        await self._apply_easing_config_to_server(
            "dial",
            preset_config["dial_easing_period"],
            preset_config["dial_easing_step"],
        )
        await self._apply_easing_config_to_server(
            "backlight",
            preset_config["backlight_easing_period"],
            preset_config["backlight_easing_step"],
        )
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

        _LOGGER.info("Applied %s behavior preset to dial %s", preset_key, self._dial_uid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        config_manager = async_get_config_manager(self.hass)
        config = config_manager.get_dial_config(self._dial_uid)

        # Find current preset or custom
        current = self.current_option
        description = "Custom configuration"
        for preset_key, preset_data in BEHAVIOR_PRESETS.items():
            if preset_key == current:
                description = preset_data["description"]
                break

        return {
            "description": description,
            "dial_easing_period": config.get("dial_easing_period", 50),
            "dial_easing_step": config.get("dial_easing_step", 5),
            "backlight_easing_period": config.get("backlight_easing_period", 50),
            "backlight_easing_step": config.get("backlight_easing_step", 5),
        }
