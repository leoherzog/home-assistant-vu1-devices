"""Configuration entities for VU1 dials."""
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from .const import UPDATE_MODE_AUTOMATIC, UPDATE_MODE_MANUAL, VU1DialEntity
from .coordinator import _get_dial_client_and_coordinator
from .device_config import async_get_config_manager
from .sensor_binding import async_get_binding_manager

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "VU1ConfigEntityBase",
    "VU1ConfigNumber",
    "CONFIG_NUMBER_DESCRIPTIONS",
    "VU1UpdateModeSensor",
    "VU1BoundEntitySensor",
]


class VU1ConfigEntityBase(VU1DialEntity, CoordinatorEntity):
    """Base class for VU1 configuration entities.

    Inherits ``device_info`` and ``available`` from the ``VU1DialEntity``
    mixin (the canonical source per AGENTS.md); ``VU1DialEntity`` precedes
    ``CoordinatorEntity`` in the MRO so the mixin's ``super()`` calls resolve
    to ``CoordinatorEntity``.
    """

    def __init__(self, coordinator, dial_uid: str) -> None:
        """Initialize the config entity."""
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._config_manager = async_get_config_manager(coordinator.hass)
        self._attr_entity_category = EntityCategory.CONFIG
        # _attr_has_entity_name is inherited from VU1DialEntity.

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
        if dial_uid == self._dial_uid:
            # Update local state from configuration and trigger UI update
            await self._sync_from_config()
            self.async_schedule_update_ha_state()

    async def _sync_from_config(self) -> None:
        """Sync entity state from configuration. Override in subclasses."""
        pass

    async def _update_config(self, **config_updates) -> None:
        """Update dial configuration with optimized sensor binding handling."""
        current_config = self._config_manager.get_dial_config(self._dial_uid)
        new_config = {**current_config, **config_updates}
        
        # Save the configuration first
        await self._config_manager.async_update_dial_config(self._dial_uid, new_config)
        
        # Only update sensor bindings if binding-related keys changed
        binding_keys = {"bound_entity", "update_mode", "value_min", "value_max"}
        if any(key in config_updates for key in binding_keys):
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
        result = _get_dial_client_and_coordinator(self.hass, self._dial_uid)
        
        if not result:
            _LOGGER.error("Failed to get client/coordinator for dial %s - cannot apply easing config", self._dial_uid)
            raise HomeAssistantError(f"Cannot communicate with dial {self._dial_uid}")
            
        client, coordinator = result

        # Mark grace period immediately after getting valid client/coordinator
        # This prevents sync loops even if subsequent operations fail
        coordinator.mark_behavior_change_from_ha(self._dial_uid)

        config = self._config_manager.get_dial_config(self._dial_uid)
        
        # Determine which config keys and API method to use
        if easing_type == "dial":
            period_key = "dial_easing_period"
            step_key = "dial_easing_step"
            default_period = 50
            default_step = 5
            api_method = client.set_dial_easing
        elif easing_type == "backlight":
            period_key = "backlight_easing_period"
            step_key = "backlight_easing_step"
            default_period = 50
            default_step = 5
            api_method = client.set_backlight_easing
        else:
            raise ValueError(f"Invalid easing_type: {easing_type}")
        
        # Use new values if provided, otherwise use current config
        period = new_period if new_period is not None else config.get(period_key, default_period)
        step = new_step if new_step is not None else config.get(step_key, default_step)
        
        try:
            _LOGGER.info("Setting %s easing for %s: period=%d, step=%d", easing_type, self._dial_uid, period, step)
            await api_method(self._dial_uid, period, step)
        except Exception as err:
            _LOGGER.error("Failed to set %s easing for %s: %s", easing_type, self._dial_uid, err)
            raise HomeAssistantError(f"Failed to apply {easing_type} easing: {err}")

@dataclass(frozen=True, kw_only=True)
class VU1ConfigNumberDescription:
    """Describes a per-dial configuration number entity."""

    key: str
    name: str
    native_min_value: float
    native_max_value: float
    native_step: float
    default: float
    cast: Callable[[float], float]
    native_unit_of_measurement: str | None = None
    easing_type: str | None = None
    easing_param: str | None = None


CONFIG_NUMBER_DESCRIPTIONS: tuple[VU1ConfigNumberDescription, ...] = (
    VU1ConfigNumberDescription(
        key="value_min",
        name="Value range minimum",
        native_min_value=-1000,
        native_max_value=1000,
        native_step=0.1,
        default=0,
        cast=float,
    ),
    VU1ConfigNumberDescription(
        key="value_max",
        name="Value range maximum",
        native_min_value=-1000,
        native_max_value=1000,
        native_step=0.1,
        default=100,
        cast=float,
    ),
    VU1ConfigNumberDescription(
        key="dial_easing_period",
        name="Dial easing period",
        native_min_value=10,
        native_max_value=1000,
        native_step=10,
        native_unit_of_measurement="ms",
        default=50,
        cast=int,
        easing_type="dial",
        easing_param="period",
    ),
    VU1ConfigNumberDescription(
        key="dial_easing_step",
        name="Dial easing step",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        default=5,
        cast=int,
        easing_type="dial",
        easing_param="step",
    ),
    VU1ConfigNumberDescription(
        key="backlight_easing_period",
        name="Backlight easing period",
        native_min_value=10,
        native_max_value=1000,
        native_step=10,
        native_unit_of_measurement="ms",
        default=50,
        cast=int,
        easing_type="backlight",
        easing_param="period",
    ),
    VU1ConfigNumberDescription(
        key="backlight_easing_step",
        name="Backlight easing step",
        native_min_value=1,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement="%",
        default=5,
        cast=int,
        easing_type="backlight",
        easing_param="step",
    ),
)


class VU1ConfigNumber(VU1ConfigEntityBase, NumberEntity):
    """Per-dial configuration number entity driven by a description.

    Consolidates the six former classes (value min/max and the four easing
    period/step numbers). Unique-id suffixes and float-vs-int casting are
    preserved exactly via the description's ``key`` and ``cast`` fields.
    """

    def __init__(
        self,
        coordinator,
        dial_uid: str,
        description: VU1ConfigNumberDescription,
    ) -> None:
        """Initialize the configuration number."""
        super().__init__(coordinator, dial_uid)
        self._description = description
        self._attr_unique_id = f"{dial_uid}_{description.key}"
        # translation_key derives from the description key; the entity name comes
        # from strings.json / translations under entity.number.<key>.name. The
        # icon now lives in icons.json (entity.number.<key>.default).
        self._attr_translation_key = description.key
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        if description.native_unit_of_measurement is not None:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        # Initialize with current config value
        config = self._config_manager.get_dial_config(dial_uid)
        self._attr_native_value = config.get(description.key, description.default)

    async def _sync_from_config(self) -> None:
        """Sync from configuration."""
        config = self._config_manager.get_dial_config(self._dial_uid)
        self._attr_native_value = config.get(
            self._description.key, self._description.default
        )

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        old_value = self._attr_native_value
        new_value = self._description.cast(value)

        # Immediately update local state for responsive UI
        self._attr_native_value = new_value
        self.async_write_ha_state()

        try:
            # Easing numbers apply to hardware first, then persist on success.
            if self._description.easing_type is not None:
                await self._apply_easing_config_to_server(
                    self._description.easing_type,
                    **{f"new_{self._description.easing_param}": new_value},
                )
            await self._update_config(**{self._description.key: new_value})
        except Exception as err:
            # Rollback on error
            self._attr_native_value = old_value
            self.async_write_ha_state()
            raise HomeAssistantError(
                f"Failed to update {self._description.name.lower()}: {err}"
            )

class VU1UpdateModeSensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing current update mode."""

    def __init__(self, coordinator, dial_uid: str) -> None:
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
        if not self.hass:
            return None

        config = self._config_manager.get_dial_config(self._dial_uid)
        return config.get("update_mode", UPDATE_MODE_MANUAL)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.hass:
            return {}
            
        config = self._config_manager.get_dial_config(self._dial_uid)
        
        attrs = {
            "update_mode": config.get("update_mode", "manual"),
        }
        
        if config.get("update_mode") == "automatic":
            attrs.update({
                "bound_entity": config.get("bound_entity"),
                "value_min": config.get("value_min", 0),
                "value_max": config.get("value_max", 100),
            })
        
        return attrs

class VU1BoundEntitySensor(VU1ConfigEntityBase, SensorEntity):
    """Sensor showing currently bound entity."""

    def __init__(self, coordinator, dial_uid: str) -> None:
        """Initialize the bound entity sensor."""
        super().__init__(coordinator, dial_uid)
        self._attr_unique_id = f"{dial_uid}_bound_entity_status"
        self._attr_translation_key = "bound_entity"
        self._attr_entity_category = None

    @property
    def native_value(self) -> str | None:
        """Return the entity_id currently bound to the dial."""
        if not self.hass:
            return None

        config = self._config_manager.get_dial_config(self._dial_uid)

        if config.get("update_mode") != "automatic":
            return None

        return config.get("bound_entity")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if not self.hass:
            return {}

        config = self._config_manager.get_dial_config(self._dial_uid)

        attrs = {
            "update_mode": config.get("update_mode", "manual"),
            "bound_entity_id": config.get("bound_entity"),
        }

        # Add current sensor value if bound
        bound_entity = config.get("bound_entity")
        if bound_entity and config.get("update_mode") == "automatic":
            state = self.hass.states.get(bound_entity)
            if state:
                attrs.update({
                    "bound_entity_name": state.attributes.get("friendly_name"),
                    "sensor_state": state.state,
                    "sensor_unit": state.attributes.get("unit_of_measurement"),
                    "last_updated": state.last_updated.isoformat(),
                })

        return attrs
