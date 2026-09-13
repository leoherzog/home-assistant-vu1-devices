"""Configuration entities for VU1 dials."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import EntityCategory

from .const import (
    CONF_UPDATE_MODE,
    CONF_VALUE_MAX,
    CONF_VALUE_MIN,
    DOMAIN,
    UPDATE_MODE_AUTOMATIC,
    UPDATE_MODE_MANUAL,
)
from .coordinator import VU1DataUpdateCoordinator
from .device_config import async_get_config_manager
from .entity import VU1DialEntity
from .sensor_binding import BINDING_KEYS, async_get_binding_manager

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "CONFIG_NUMBER_DESCRIPTIONS",
    "VU1BoundEntitySensor",
    "VU1ConfigEntityBase",
    "VU1ConfigNumber",
    "VU1UpdateModeSensor",
]


class VU1ConfigEntityBase(VU1DialEntity):
    """Base class for VU1 configuration entities."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the config entity."""
        super().__init__(coordinator, dial_uid)
        self._config_manager = async_get_config_manager(coordinator.hass)
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        """Register for configuration change notifications."""
        await super().async_added_to_hass()
        
        # Register as a listener for configuration changes
        self._config_manager.async_add_listener(self._dial_uid, self._on_config_change)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from configuration change notifications."""
        await super().async_will_remove_from_hass()
        
        # Unregister as a listener
        self._config_manager.async_remove_listener(self._dial_uid, self._on_config_change)

    async def _on_config_change(self, dial_uid: str, config: dict[str, Any]) -> None:
        """Handle configuration changes from external sources."""
        self.async_write_ha_state()

    async def _update_config(self, **config_updates) -> None:
        """Update dial configuration with optimized sensor binding handling."""
        await self._config_manager.async_update_dial_config(self._dial_uid, config_updates)

        # Only update sensor bindings if binding-related keys changed
        if any(key in config_updates for key in BINDING_KEYS):
            binding_manager = async_get_binding_manager(self.hass)
            await binding_manager.async_reconfigure_dial_binding(self._dial_uid)

    async def _apply_easing_config_to_server(
        self, 
        easing_type: str, 
        new_period: int | None = None,
        new_step: int | None = None
    ) -> None:
        """Apply easing configuration to server with specific new values.
        
        Args:
            easing_type: Either "dial" or "backlight" to specify which easing to configure
            new_period: New period value, or None to use current config
            new_step: New step value, or None to use current config
        """
        _LOGGER.debug("Attempting to apply %s easing config for %s", easing_type, self._dial_uid)
        client = self.coordinator.client
        self.coordinator.mark_behavior_change_from_ha(self._dial_uid)

        config = self._config_manager.get_dial_config(self._dial_uid)
        api_method = client.set_dial_easing if easing_type == "dial" else client.set_backlight_easing
        period = new_period if new_period is not None else config[f"{easing_type}_easing_period"]
        step = new_step if new_step is not None else config[f"{easing_type}_easing_step"]

        _LOGGER.info("Setting %s easing for %s: period=%d, step=%d", easing_type, self._dial_uid, period, step)
        await api_method(self._dial_uid, period, step)

@dataclass(frozen=True, kw_only=True)
class VU1ConfigNumberDescription:
    """Describes a per-dial configuration number entity."""

    key: str
    native_min_value: float
    native_max_value: float
    native_step: float
    cast: Callable[[float], float]
    native_unit_of_measurement: str | None = None
    mode: NumberMode = NumberMode.AUTO
    easing_type: str | None = None
    easing_param: str | None = None


CONFIG_NUMBER_DESCRIPTIONS: tuple[VU1ConfigNumberDescription, ...] = (
    VU1ConfigNumberDescription(
        key="value_min",
        native_min_value=-1e9,
        native_max_value=1e9,
        native_step=0.1,
        mode=NumberMode.BOX,
        cast=float,
    ),
    VU1ConfigNumberDescription(
        key="value_max",
        native_min_value=-1e9,
        native_max_value=1e9,
        native_step=0.1,
        mode=NumberMode.BOX,
        cast=float,
    ),
    VU1ConfigNumberDescription(
        key="dial_easing_period",
        native_min_value=10,
        native_max_value=1000,
        native_step=10,
        native_unit_of_measurement="ms",
        cast=int,
        easing_type="dial",
        easing_param="period",
    ),
    VU1ConfigNumberDescription(
        key="dial_easing_step",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        cast=int,
        easing_type="dial",
        easing_param="step",
    ),
    VU1ConfigNumberDescription(
        key="backlight_easing_period",
        native_min_value=10,
        native_max_value=1000,
        native_step=10,
        native_unit_of_measurement="ms",
        cast=int,
        easing_type="backlight",
        easing_param="period",
    ),
    VU1ConfigNumberDescription(
        key="backlight_easing_step",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        cast=int,
        easing_type="backlight",
        easing_param="step",
    ),
)


class VU1ConfigNumber(VU1ConfigEntityBase, NumberEntity):
    """Per-dial configuration number entity driven by a description."""

    def __init__(
        self,
        coordinator: VU1DataUpdateCoordinator,
        dial_uid: str,
        description: VU1ConfigNumberDescription,
    ) -> None:
        """Initialize the configuration number."""
        super().__init__(coordinator, dial_uid)
        self._description = description
        self._attr_unique_id = f"{dial_uid}_{description.key}"
        self._attr_translation_key = description.key
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_mode = description.mode
        if description.native_unit_of_measurement is not None:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement

    @property
    def native_value(self) -> float:
        """Return the stored value."""
        return self._config_manager.get_dial_config(self._dial_uid)[self._description.key]

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        new_value = self._description.cast(value)

        if self._description.key in (CONF_VALUE_MIN, CONF_VALUE_MAX):
            config = {**self._config_manager.get_dial_config(self._dial_uid), self._description.key: new_value}
            if config[CONF_VALUE_MIN] >= config[CONF_VALUE_MAX]:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="value_min_not_less_than_max"
                )

        if self._description.easing_type is not None:
            await self._apply_easing_config_to_server(
                self._description.easing_type,
                **{f"new_{self._description.easing_param}": new_value},
            )
        await self._update_config(**{self._description.key: new_value})

class VU1UpdateModeSensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing current update mode."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the update mode sensor."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_update_mode_status"
        self._attr_translation_key = "update_mode"
        self._attr_entity_category = None
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = [UPDATE_MODE_AUTOMATIC, UPDATE_MODE_MANUAL]

    @property
    def native_value(self) -> str | None:
        """Return the current update mode."""
        return self._config_manager.get_dial_config(self._dial_uid)[CONF_UPDATE_MODE]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        attrs = {"update_mode": config[CONF_UPDATE_MODE]}
        if config[CONF_UPDATE_MODE] == UPDATE_MODE_AUTOMATIC:
            attrs.update({
                "bound_entity": config["bound_entity"],
                "value_min": config["value_min"],
                "value_max": config["value_max"],
            })
        return attrs

class VU1BoundEntitySensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing currently bound entity."""

    def __init__(self, coordinator: VU1DataUpdateCoordinator, dial_uid: str) -> None:
        """Initialize the bound entity sensor."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_bound_entity_status"
        self._attr_translation_key = "bound_entity"
        self._attr_entity_category = None

    @property
    def native_value(self) -> str | None:
        """Return the entity_id currently bound to the dial."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        if config[CONF_UPDATE_MODE] != UPDATE_MODE_AUTOMATIC:
            return None
        return config["bound_entity"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        return {
            "update_mode": config[CONF_UPDATE_MODE],
            "bound_entity_id": config["bound_entity"],
        }
