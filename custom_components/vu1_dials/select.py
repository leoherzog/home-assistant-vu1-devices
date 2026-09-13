"""Support for VU1 dial behavior preset select entities."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .config_entities import VU1ConfigEntityBase
from .const import BEHAVIOR_PRESETS
from .entity import async_setup_dial_entities

if TYPE_CHECKING:
    from . import VU1ConfigEntry, VU1DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

__all__ = ["async_setup_entry"]

PARALLEL_UPDATES = 1

EASING_KEYS = ("dial_easing_period", "dial_easing_step", "backlight_easing_period", "backlight_easing_step")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: VU1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VU1 select entities."""
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str) -> list:
        return [VU1BehaviorSelect(coordinator, dial_uid)]

    async_setup_dial_entities(config_entry, async_add_entities, entity_factory)


class VU1BehaviorSelect(VU1ConfigEntityBase, SelectEntity):
    """Select entity for dial behavior presets."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the behavior select entity."""
        # VU1ConfigEntityBase provides the config-change listener lifecycle,
        # device_info, availability, and the CONFIG entity category.
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_behavior_preset"
        self._attr_translation_key = "behavior_preset"
        # Option values are the snake_case preset slugs ("responsive",
        # "balanced", ...) so HA applies the entity.select.<key>.state.<option>
        # translations for the reported state. Easing values that match no
        # preset report no option.
        self._attr_options = list(BEHAVIOR_PRESETS)

    @property
    def current_option(self) -> str | None:
        """Return the preset matching the dial's easing config, if any."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        return next(
            (
                option
                for option in self.options
                if all(config.get(key) == BEHAVIOR_PRESETS[option][key] for key in EASING_KEYS)
            ),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        preset_config = BEHAVIOR_PRESETS[option]

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
        await self._config_manager.async_update_dial_config(
            self._dial_uid, {key: preset_config[key] for key in EASING_KEYS}
        )

        _LOGGER.info("Applied %s behavior preset to dial %s", option, self._dial_uid)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        current = self.current_option
        return {
            "description": BEHAVIOR_PRESETS[current]["description"] if current else None,
            **{key: config.get(key) for key in EASING_KEYS},
        }
