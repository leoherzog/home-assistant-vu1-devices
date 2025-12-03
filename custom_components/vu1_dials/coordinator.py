"""DataUpdateCoordinator for VU1 Dials integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .vu1_api import VU1APIClient, VU1APIError

_LOGGER = logging.getLogger(__name__)

__all__ = ["VU1DataUpdateCoordinator"]


class VU1DataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching VU1 data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: VU1APIClient,
        update_interval: timedelta,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        # Track last known names to detect server-side changes
        self._previous_dial_names: dict[str, str] = {}
        # Prevent sync loops when name changes originate from HA
        self._name_change_grace_periods: dict[str, Any] = {}
        self._behavior_change_grace_periods: dict[str, Any] = {}
        self._grace_period_seconds = 10
        # Store device identifier string for via_device relationships, not internal device.id
        self.server_device_identifier: str | None = None
        # Binding manager reference (set later)
        self._binding_manager: Any = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from VU1 server."""
        try:
            dials = await self.client.get_dial_list()

            if not isinstance(dials, list):
                _LOGGER.error("Unexpected dial list format: %s", type(dials))
                raise UpdateFailed("Invalid dial list format")

            # Get detailed status for each dial
            dial_data: dict[str, Any] = {}
            dial_refs: list[tuple[str, dict[str, Any]]] = []
            dial_tasks: list[Any] = []

            for dial in dials:
                if not isinstance(dial, dict) or "uid" not in dial:
                    _LOGGER.warning("Invalid dial data: %s", dial)
                    continue

                dial_uid = dial["uid"]
                dial_refs.append((dial_uid, dial))
                dial_tasks.append(self.client.get_dial_status(dial_uid))

            if dial_tasks:
                results = await asyncio.gather(*dial_tasks, return_exceptions=True)
            else:
                results = []

            for (dial_uid, dial), result in zip(dial_refs, results):
                if isinstance(result, BaseException):
                    if isinstance(result, VU1APIError):
                        _LOGGER.warning("Failed to get status for dial %s: %s", dial_uid, result)
                    elif isinstance(result, asyncio.CancelledError):
                        _LOGGER.debug("Status update cancelled for dial %s", dial_uid)
                    else:
                        _LOGGER.error("Unexpected error getting status for dial %s", dial_uid, exc_info=result)
                    dial_data[dial_uid] = {**dial, "detailed_status": {}}
                    continue

                status: dict[str, Any] = result
                dial_data[dial_uid] = {**dial, "detailed_status": status}

                await self._sync_name_from_server(dial_uid, dial.get("dial_name"))
                await self._check_server_behavior_change(dial_uid, status)

            if hasattr(self, '_binding_manager') and self._binding_manager:
                await self._binding_manager.async_update_bindings({"dials": dial_data})

            return {"dials": dial_data}

        except VU1APIError as err:
            _LOGGER.error("VU1 API error: %s", err)
            raise UpdateFailed(f"Error communicating with VU1 server: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error updating VU1 data")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    def set_binding_manager(self, binding_manager: Any) -> None:
        """Set the binding manager reference."""
        self._binding_manager = binding_manager

    async def _sync_name_from_server(self, dial_uid: str, server_name: str | None) -> None:
        """Sync device name from server to Home Assistant if it has changed."""
        if not server_name:
            return

        # Check if we're in a grace period (change originated from HA)
        current_time = dt_util.utcnow()
        grace_end = self._name_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring server name change for %s during grace period", dial_uid)
            return

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, dial_uid)})

        if device and not device.name_by_user and device.name != server_name:
            _LOGGER.info("Server name for %s changed ('%s' -> '%s'). Updating device.", dial_uid, device.name, server_name)
            device_registry.async_update_device(device.id, name=server_name)

        self._previous_dial_names[dial_uid] = server_name

    def mark_name_change_from_ha(self, dial_uid: str) -> None:
        """Mark that a name change originated from HA to prevent sync loops."""
        grace_end = dt_util.utcnow() + timedelta(seconds=self._grace_period_seconds)
        self._name_change_grace_periods[dial_uid] = grace_end
        _LOGGER.debug("Started name change grace period for %s until %s", dial_uid, grace_end.isoformat())

    async def async_set_dial_name(self, dial_uid: str, new_name: str) -> None:
        """Set the dial name on the server and update HA. Centralized method."""
        # Mark that this change originated from HA to prevent sync loops
        self.mark_name_change_from_ha(dial_uid)

        try:
            # 1. Update the VU1 Server
            await self.client.set_dial_name(dial_uid, new_name)
            # 2. Update our internal tracker
            self._previous_dial_names[dial_uid] = new_name

            # 3. Update the HA device registry
            device_registry = dr.async_get(self.hass)
            device = device_registry.async_get_device(identifiers={(DOMAIN, dial_uid)})
            if device:
                device_registry.async_update_device(device.id, name=new_name)

            _LOGGER.info("Successfully synced name '%s' to server for dial %s", new_name, dial_uid)
            # 4. Refresh coordinator to ensure consistency
            await self.async_request_refresh()

        except VU1APIError as err:
            _LOGGER.error("Failed to set dial name for %s on server: %s", dial_uid, err)
            # Clear grace period on failure to allow future updates
            self._name_change_grace_periods.pop(dial_uid, None)
            raise

    async def _handle_device_name_change(self, dial_uid: str, new_name: str) -> None:
        """Handle device name change from HA UI."""
        # Check if we're in a grace period (change originated from server)
        current_time = dt_util.utcnow()
        grace_end = self._name_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring HA name change for %s during grace period", dial_uid)
            return

        # Check if name actually changed
        if self._previous_dial_names.get(dial_uid) == new_name:
            return

        _LOGGER.info("Device name changed in HA for dial %s: '%s'", dial_uid, new_name)

        # Sync to server using existing method
        try:
            await self.async_set_dial_name(dial_uid, new_name)
        except Exception as err:
            _LOGGER.error("Failed to sync device name to server: %s", err)

    def mark_behavior_change_from_ha(self, dial_uid: str) -> None:
        """Mark that a behavior change originated from HA to prevent sync loops."""
        grace_end = dt_util.utcnow() + timedelta(seconds=self._grace_period_seconds)
        self._behavior_change_grace_periods[dial_uid] = grace_end
        _LOGGER.debug(
            "Started behavior grace period for %s until %s",
            dial_uid, grace_end.isoformat()
        )

    async def _check_server_behavior_change(self, dial_uid: str, status: dict[str, Any]) -> None:
        """Check if server behavior settings changed and sync to HA."""
        if not status:
            return

        current_time = dt_util.utcnow()
        grace_end = self._behavior_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring server behavior change for %s during grace period", dial_uid)
            return

        easing_config = status.get("easing", {})
        if not easing_config:
            return

        from .device_config import async_get_config_manager
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(dial_uid)
        # Convert server values to int with fallbacks for invalid data
        try:
            dial_period = int(easing_config.get("dial_period", 50))
        except (ValueError, TypeError):
            dial_period = 50

        try:
            backlight_period = int(easing_config.get("backlight_period", 50))
        except (ValueError, TypeError):
            backlight_period = 50

        try:
            dial_step = int(easing_config.get("dial_step", 5))
        except (ValueError, TypeError):
            dial_step = 5

        try:
            backlight_step = int(easing_config.get("backlight_step", 5))
        except (ValueError, TypeError):
            backlight_step = 5

        server_values = {
            "dial_easing_period": dial_period,
            "dial_easing_step": dial_step,
            "backlight_easing_period": backlight_period,
            "backlight_easing_step": backlight_step,
        }

        config_changed = False
        for key, server_value in server_values.items():
            if current_config.get(key) != server_value:
                config_changed = True
                _LOGGER.info(
                    "Server %s changed for %s: %s -> %s",
                    key, dial_uid, current_config.get(key), server_value
                )

        if config_changed:
            # Update HA config to match server values
            updated_config = {**current_config, **server_values}
            await config_manager.async_update_dial_config(dial_uid, updated_config)
            _LOGGER.info("Synced behavior settings from server for %s", dial_uid)
