"""DataUpdateCoordinator for VU1 Dials integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BACKLIGHT_CHANNELS,
    CONF_BACKLIGHT_COLOR,
    CONF_BACKLIGHT_LAST_COLOR,
    DATA_BINDING_MANAGER,
    DOMAIN,
)
from .device_config import async_get_config_manager
from .vu1_api import VU1APIClient, VU1APIError, VU1AuthError, VU1InvalidNameError

if TYPE_CHECKING:
    from . import VU1ConfigEntry

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "VU1DataUpdateCoordinator",
    "_get_dial_client_and_coordinator",
    "dial_server_name",
    "image_basename",
    "server_version",
]

BLANK_IMAGE_CRC = "00000000"

# (server easing key, dial config key, server database default)
SERVER_EASING_FIELDS: tuple[tuple[str, str, int], ...] = (
    ("dial_period", "dial_easing_period", 50),
    ("dial_step", "dial_easing_step", 2),
    ("backlight_period", "backlight_easing_period", 100),
    ("backlight_step", "backlight_easing_step", 5),
)


def dial_server_name(dial: dict[str, Any]) -> str | None:
    """Return the server's dial name, or None for an empty name or the server's "Not set" placeholder."""
    name = (dial.get("dial_name") or "").strip()
    return name if name and name.casefold() != "not set" else None


def server_version(value: Any) -> str | None:
    """Return a version or hash string, or None for an empty value or the server's "?" placeholder."""
    return value if value and value != "?" else None


def image_basename(image_file: str | None) -> str | None:
    """Return the file name of a server image path, which may be absolute."""
    return image_file.replace("\\", "/").rsplit("/", 1)[-1] if image_file else None


class VU1DataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching VU1 data."""

    config_entry: VU1ConfigEntry
    hub_device_id: str

    def __init__(
        self,
        hass: HomeAssistant,
        client: VU1APIClient,
        update_interval: timedelta,
        config_entry: VU1ConfigEntry,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=update_interval,
            always_update=False,
        )
        self.client = client
        # Last known server name per dial, so an HA rename that matches it is not re-sent
        self._previous_dial_names: dict[str, str] = {}
        # Prevent sync loops when name changes originate from HA
        self._name_change_grace_periods: dict[str, Any] = {}
        self._behavior_change_grace_periods: dict[str, Any] = {}
        self._grace_period_seconds = 10
        # Identifier of the hub device in the device registry
        self.server_device_identifier = f"vu1_server_{config_entry.entry_id}"
        # Dials whose status fetch is failing, so the failure is logged once
        self._failed_dials: set[str] = set()
        # Dials with an open dial_unresponsive repair issue
        self._unresponsive_dials: set[str] = set()
        # Status fields commanded since the last poll started, per dial
        self._commanded: dict[str, dict[str, Any]] = {}

    def _prune_expired_grace_periods(self) -> None:
        """Remove expired entries from grace period dicts to prevent unbounded growth."""
        now = dt_util.utcnow()
        for d in (self._name_change_grace_periods, self._behavior_change_grace_periods):
            expired = [k for k, v in d.items() if v <= now]
            for k in expired:
                del d[k]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from VU1 server."""
        self._prune_expired_grace_periods()
        self._commanded = {}
        try:
            dials = await self.client.get_dial_list()

            if not isinstance(dials, list):
                raise UpdateFailed(translation_domain=DOMAIN, translation_key="not_vu1_server", translation_placeholders={"url": self.client.base_url})

            dials = [dial for dial in dials if isinstance(dial, dict) and "uid" in dial]
            results = await asyncio.gather(
                *(self.client.get_dial_status(dial["uid"]) for dial in dials),
                *(self._async_get_image_crc(dial) for dial in dials),
                return_exceptions=True,
            )

            dial_data: dict[str, Any] = {}
            for dial, status, image_crc in zip(dials, results, results[len(dials):]):
                dial_uid = dial["uid"]
                if isinstance(image_crc, BaseException):
                    image_crc = None

                if isinstance(status, BaseException):
                    if dial_uid not in self._failed_dials:
                        self._failed_dials.add(dial_uid)
                        _LOGGER.info("Failed to get status for dial %s: %s", dial_uid, status)
                    status = {}
                elif dial_uid in self._failed_dials:
                    self._failed_dials.discard(dial_uid)
                    _LOGGER.info("Status for dial %s is available again", dial_uid)

                dial_data[dial_uid] = {**dial, "detailed_status": status, "image_crc": image_crc}

                if status:
                    self._update_unresponsive_issue(dial_uid, dial, status)
                    self._sync_device_registry(dial_uid, dial, status)
                    await self._check_server_behavior_change(dial_uid, status)
                    await self._async_restore_backlight(dial_uid, status)

            for dial_uid in self._unresponsive_dials - dial_data.keys():
                self._delete_unresponsive_issue(dial_uid)

            if dial_data:
                ir.async_delete_issue(self.hass, DOMAIN, "no_dials")
            else:
                ir.async_create_issue(
                    self.hass, DOMAIN, "no_dials",
                    is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="no_dials",
                )

            await self.hass.data[DATA_BINDING_MANAGER].async_update_bindings({"dials": dial_data})

            # A command sent while this poll ran is newer than the status it fetched.
            for dial_uid, fields in self._commanded.items():
                if dial_uid in dial_data:
                    dial_data[dial_uid]["detailed_status"].update(fields)

            return {"dials": dial_data}

        except VU1AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except VU1APIError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_get_image_crc(self, dial: dict[str, Any]) -> str | None:
        """Return the dial's image CRC; the server logs an error for each CRC request on a blank dial."""
        if image_basename(dial.get("image_file")) == "img_blank":
            return BLANK_IMAGE_CRC
        return await self.client.get_dial_image_crc(dial["uid"])

    def _update_unresponsive_issue(self, dial_uid: str, dial: dict[str, Any], status: dict[str, Any]) -> None:
        """Open or close the repair issue for a dial the server stopped retrying."""
        if not (status.get("value_unresponsive") or status.get("backlight_unresponsive")):
            self._delete_unresponsive_issue(dial_uid)
            return
        self._unresponsive_dials.add(dial_uid)
        ir.async_create_issue(
            self.hass, DOMAIN, f"dial_unresponsive_{dial_uid}",
            is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="dial_unresponsive",
            translation_placeholders={"dial_name": self._dial_display_name(dial_uid, dial)},
        )

    def _dial_display_name(self, dial_uid: str, dial: dict[str, Any]) -> str:
        """Return the dial's HA device name, else its server name, else ``VU1 Dial <uid>``."""
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, dial_uid), self.config_entry.entry_id
        )
        return (
            (device and (device.name_by_user or device.name))
            or dial_server_name(dial)
            or f"VU1 Dial {dial_uid}"
        )

    def _delete_unresponsive_issue(self, dial_uid: str) -> None:
        """Close the dial_unresponsive repair issue for a dial."""
        self._unresponsive_dials.discard(dial_uid)
        ir.async_delete_issue(self.hass, DOMAIN, f"dial_unresponsive_{dial_uid}")

    @callback
    def async_clear_issues(self) -> None:
        """Close every per-dial repair issue this coordinator opened; call on unload."""
        for dial_uid in list(self._unresponsive_dials):
            self._delete_unresponsive_issue(dial_uid)

    def _sync_device_registry(
        self, dial_uid: str, dial: dict[str, Any], status: dict[str, Any]
    ) -> None:
        """Sync the server-side name and firmware/hardware versions to the device registry."""
        server_name = dial_server_name(dial)
        # Ignore server names during a grace period (change originated from HA)
        grace_end = self._name_change_grace_periods.get(dial_uid)
        if grace_end and dt_util.utcnow() < grace_end:
            server_name = None
        elif server_name:
            self._previous_dial_names[dial_uid] = server_name

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device_by_identifier((DOMAIN, dial_uid), self.config_entry.entry_id)
        if device is None:
            return

        changes = {
            "name": None if device.name_by_user else server_name,
            "sw_version": server_version(status.get("fw_version")),
            "hw_version": server_version(status.get("hw_version")),
        }
        changes = {key: value for key, value in changes.items() if value and getattr(device, key) != value}
        if changes:
            device_registry.async_update_device(device.id, **changes)

    async def _async_restore_backlight(self, dial_uid: str, status: dict[str, Any]) -> None:
        """Re-apply the stored backlight when the server reports it reset to off.

        The VU1 server does not persist backlight across restarts or provisioning.
        """
        backlight = status.get("backlight")
        color = async_get_config_manager(self.hass).get_dial_config(dial_uid)[CONF_BACKLIGHT_COLOR]
        if not backlight or any(backlight.values()) or not any(color):
            return
        try:
            await self.client.set_dial_backlight(dial_uid, *color)
        except VU1APIError as err:
            _LOGGER.debug("Failed to restore backlight for dial %s: %s", dial_uid, err)
            return
        status["backlight"] = dict(zip(BACKLIGHT_CHANNELS, color))

    async def async_set_backlight(
        self, dial_uid: str, red: int, green: int, blue: int, white: int | None = None
    ) -> None:
        """Set a dial's RGBW backlight, persist it, and publish it optimistically.

        ``white=None`` keeps the stored white channel.
        """
        config_manager = async_get_config_manager(self.hass)
        if white is None:
            white = config_manager.get_dial_config(dial_uid)[CONF_BACKLIGHT_COLOR][3]
        await self.client.set_dial_backlight(dial_uid, red, green, blue, white)
        color = [red, green, blue, white]
        # backlight_color stays all zero while off, or _async_restore_backlight would turn the light back on.
        update = {CONF_BACKLIGHT_COLOR: color}
        if any(color):
            update[CONF_BACKLIGHT_LAST_COLOR] = color
        await config_manager.async_update_dial_config(dial_uid, update)
        self._async_publish(dial_uid, backlight=dict(zip(BACKLIGHT_CHANNELS, color)))

    async def async_set_dial_value(self, dial_uid: str, value: int) -> None:
        """Set a dial's value and publish it optimistically."""
        await self.client.set_dial_value(dial_uid, value)
        self._async_publish(dial_uid, value=value)

    @callback
    def _async_publish(self, dial_uid: str, **fields: Any) -> None:
        """Write commanded status fields into coordinator data, keeping them over a poll in flight."""
        self._commanded.setdefault(dial_uid, {}).update(fields)
        if dial := self.data["dials"].get(dial_uid):
            dial["detailed_status"].update(fields)
            self.async_update_listeners()

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
        except VU1APIError:
            # Clear grace period on failure to allow future updates
            self._name_change_grace_periods.pop(dial_uid, None)
            raise

        # 2. Update our internal tracker
        self._previous_dial_names[dial_uid] = new_name

        # 3. Update the HA device registry
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device_by_identifier((DOMAIN, dial_uid), self.config_entry.entry_id)
        if device:
            device_registry.async_update_device(device.id, name=new_name)

        _LOGGER.info("Successfully synced name '%s' to server for dial %s", new_name, dial_uid)
        # 4. Refresh coordinator to ensure consistency
        await self.async_request_refresh()

    async def async_handle_ha_name_change(self, dial_uid: str, new_name: str) -> None:
        """Handle device name change originating from the HA UI.

        No grace-period check here: grace periods are only ever set by
        HA-originated changes (mark_name_change_from_ha), and nothing in the
        integration writes name_by_user, so there is no server->HA->server
        feedback loop to suppress. The grace check belongs only in
        _sync_device_registry (server->HA echo). The _previous_dial_names
        comparison below already dedupes; an additional grace check here would
        silently drop a second user rename within the grace window and leave
        HA and the server permanently desynced.
        """
        issue_id = f"invalid_dial_name_{dial_uid}"
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

        # Check if name actually changed
        if self._previous_dial_names.get(dial_uid) == new_name:
            return

        _LOGGER.info("Device name changed in HA for dial %s: '%s'", dial_uid, new_name)

        try:
            await self.async_set_dial_name(dial_uid, new_name)
        except VU1InvalidNameError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="invalid_dial_name",
                translation_placeholders={"name": new_name, "error": str(err)},
            )
        except VU1APIError as err:
            _LOGGER.warning("Failed to sync device name '%s' to server: %s", new_name, err)

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
        current_time = dt_util.utcnow()
        grace_end = self._behavior_change_grace_periods.get(dial_uid)
        if grace_end and current_time < grace_end:
            _LOGGER.debug("Ignoring server behavior change for %s during grace period", dial_uid)
            return

        easing_config = status.get("easing", {})
        if not easing_config:
            return

        server_values: dict[str, int] = {}
        for server_key, config_key, default in SERVER_EASING_FIELDS:
            try:
                server_values[config_key] = int(easing_config.get(server_key, default))
            except (ValueError, TypeError):
                server_values[config_key] = default
        config_manager = async_get_config_manager(self.hass)
        current_config = config_manager.get_dial_config(dial_uid)

        # A /reload whose hardware read failed persists zero easing, which the server sends to the dial on its next start.
        if not all(server_values.values()):
            try:
                await self.client.set_dial_easing(
                    dial_uid, current_config["dial_easing_period"], current_config["dial_easing_step"]
                )
                await self.client.set_backlight_easing(
                    dial_uid, current_config["backlight_easing_period"], current_config["backlight_easing_step"]
                )
            except VU1APIError as err:
                _LOGGER.debug("Failed to restore easing for dial %s: %s", dial_uid, err)
            return

        config_changed = False
        for key, server_value in server_values.items():
            if current_config.get(key) != server_value:
                config_changed = True
                _LOGGER.info(
                    "Server %s changed for %s: %s -> %s",
                    key, dial_uid, current_config.get(key), server_value
                )

        if config_changed:
            await config_manager.async_update_dial_config(dial_uid, server_values)
            _LOGGER.info("Synced behavior settings from server for %s", dial_uid)


def _get_dial_client_and_coordinator(hass: HomeAssistant, dial_uid: str) -> tuple[VU1APIClient, VU1DataUpdateCoordinator] | None:
    """Return the client and coordinator of the entry that reports this dial."""
    entries = hass.config_entries.async_entries(DOMAIN)
    runtime_data = getattr(entries[0], "runtime_data", None) if entries else None
    if runtime_data and runtime_data.coordinator.data and dial_uid in runtime_data.coordinator.data["dials"]:
        return runtime_data.client, runtime_data.coordinator
    return None
