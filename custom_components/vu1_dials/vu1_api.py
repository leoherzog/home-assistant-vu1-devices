"""VU1 API Client for communicating with VU1 server."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientTimeout
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

__all__ = ["DEFAULT_PORT", "DEFAULT_TIMEOUT", "VU1APIClient", "VU1APIError", "VU1AuthError", "VU1InvalidImageError", "VU1InvalidNameError", "discover_vu1_addon"]

DEFAULT_PORT = 5340
DEFAULT_TIMEOUT = 10
MAX_IMAGE_BYTES = 2 * 1024 * 1024

# Exact message the VU1 server returns (HTTP 200 + status:"fail" on dial/set and
# dial/status, HTTP 503 on setRaw/backlight/image) when a dial is offline.
OFFLINE_MESSAGE = "Invalid dial_uid or device is offline."


class VU1APIError(HomeAssistantError):
    """Base exception for VU1 API errors."""


class VU1AuthError(VU1APIError):
    """Exception raised for authentication errors (401/403)."""


class VU1InvalidNameError(VU1APIError, ServiceValidationError):
    """Exception raised when a dial name fails client-side validation."""


class VU1InvalidImageError(VU1APIError, ServiceValidationError):
    """Exception raised when image data is not a PNG/JPEG of at most 2 MB."""


API_VERSION = "v0"


class VU1APIClient:
    """Client for VU1 server API."""

    def __init__(
        self,
        host: str,
        port: int,
        api_key: str,
        session: aiohttp.ClientSession,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize VU1 API client."""
        self.host = host
        self.port = port
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = f"http://{host}:{port}"
        self._session = session

    def _auth_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return request params with the VU1 API key attached.

        The key is appended unless the caller already supplied an ``admin_key``
        (used by admin-only endpoints) or no API key is configured.
        """
        if params is None:
            params = {}
        if self.api_key and "admin_key" not in params:
            params["key"] = self.api_key
        return params

    @staticmethod
    def _check_json_status(payload: dict[str, Any]) -> None:
        """Raise the matching exception for a non-ok VU1 JSON payload.

        The server signals an offline dial with HTTP 200 + status:"fail" and the
        ``OFFLINE_MESSAGE`` body on dial/set and dial/status, so detect it here
        and surface it with the ``dial_offline`` message rather than a generic one.
        """
        if payload.get("status") != "ok":
            message = str(payload.get("message") or "Unknown error")
            if message == OFFLINE_MESSAGE:
                raise VU1APIError(translation_domain=DOMAIN, translation_key="dial_offline", translation_placeholders={"message": message})
            raise VU1APIError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"message": message})

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        form: aiohttp.FormData | None = None,
        binary: bool = False,
    ) -> Any:
        """Make an API request, returning the JSON envelope (or bytes if ``binary``)."""
        try:
            async with self._session.request(
                method,
                f"{self.base_url}/{endpoint}",
                params=self._auth_params(params),
                data=form,
                timeout=ClientTimeout(total=self.timeout),
            ) as response:
                if binary and response.ok:
                    return await response.read()
                try:
                    payload = await response.json(content_type=None)
                except ValueError:
                    payload = None
                if not response.ok:
                    message = payload.get("message") if isinstance(payload, dict) else None
                    raise self._status_error(response.status, message or response.reason)
        except (ClientError, TimeoutError) as err:
            raise VU1APIError(translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": str(err)}) from err

        if not isinstance(payload, dict):
            raise VU1APIError(translation_domain=DOMAIN, translation_key="not_vu1_server", translation_placeholders={"url": self.base_url})
        self._check_json_status(payload)
        return payload

    @staticmethod
    def _status_error(status: int, message: str | None) -> VU1APIError:
        """Convert an HTTP error status to the VU1 exception hierarchy."""
        if status in (401, 403):
            return VU1AuthError(translation_domain=DOMAIN, translation_key="invalid_auth", translation_placeholders={"message": str(message)})
        if status in (503, 406):
            return VU1APIError(translation_domain=DOMAIN, translation_key="dial_offline", translation_placeholders={"message": str(message)})
        return VU1APIError(translation_domain=DOMAIN, translation_key="api_error", translation_placeholders={"message": f"HTTP {status} {message}"})

    async def get_dial_list(self) -> list[dict[str, Any]]:
        """Get list of available dials."""
        response = await self._request("GET", f"api/{API_VERSION}/dial/list")
        return response.get("data", [])

    async def set_dial_value(self, dial_uid: str, value: int) -> None:
        """Set dial value (0-100)."""
        if not 0 <= value <= 100:
            raise ValueError("Value must be between 0 and 100")

        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/set", {"value": value})

    async def set_dial_backlight(
        self, dial_uid: str, red: int, green: int, blue: int, white: int
    ) -> None:
        """Set dial backlight RGBW values (0-100 each)."""
        for color, val in [("red", red), ("green", green), ("blue", blue), ("white", white)]:
            if not 0 <= val <= 100:
                raise ValueError(f"{color} value must be between 0 and 100")

        await self._request(
            "GET",
            f"api/{API_VERSION}/dial/{dial_uid}/backlight",
            {"red": red, "green": green, "blue": blue, "white": white},
        )

    async def get_dial_status(self, dial_uid: str) -> dict[str, Any]:
        """Get dial status."""
        response = await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/status")
        return response.get("data", {})

    async def set_dial_name(self, dial_uid: str, name: str) -> None:
        """Set dial name.

        Server requires 3-30 characters, only [a-z0-9\\-_ ] allowed.
        """
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9\-_ ]{3,30}", name, re.IGNORECASE):
            raise VU1InvalidNameError(translation_domain=DOMAIN, translation_key="invalid_dial_name")
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/name", {"name": name})

    async def get_dial_image(self, dial_uid: str) -> bytes:
        """Get dial background image."""
        return await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/image/get", binary=True)

    async def get_dial_image_crc(self, dial_uid: str) -> str | None:
        """Get the CRC32 of the dial's current background image."""
        response = await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/image/crc")
        return response.get("data")

    async def set_dial_image(self, dial_uid: str, image_data: bytes) -> None:
        """Set dial background image (PNG or JPEG, at most 2 MB) via multipart upload."""
        if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
            subtype = "png"
        elif image_data.startswith(b"\xff\xd8\xff"):
            subtype = "jpeg"
        else:
            raise VU1InvalidImageError(translation_domain=DOMAIN, translation_key="invalid_image")
        if len(image_data) > MAX_IMAGE_BYTES:
            raise VU1InvalidImageError(translation_domain=DOMAIN, translation_key="invalid_image")

        form = aiohttp.FormData()
        form.add_field("imgfile", image_data, filename=f"background.{subtype}", content_type=f"image/{subtype}")

        _LOGGER.debug("Uploading image to dial %s (%d bytes)", dial_uid, len(image_data))
        await self._request("POST", f"api/{API_VERSION}/dial/{dial_uid}/image/set", form=form)

    async def reload_dial(self, dial_uid: str) -> None:
        """Reload dial hardware info."""
        response = await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/reload")
        if response.get("data") is False:
            raise VU1APIError(translation_domain=DOMAIN, translation_key="dial_not_found", translation_placeholders={"dial_uid": dial_uid})

    async def set_dial_easing(self, dial_uid: str, period: int, step: int) -> None:
        """Set dial easing configuration."""
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/easing/dial", {"period": period, "step": step})

    async def set_backlight_easing(self, dial_uid: str, period: int, step: int) -> None:
        """Set backlight easing configuration."""
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/easing/backlight", {"period": period, "step": step})


    async def provision_new_dials(self) -> None:
        """Provision new dials that have been detected by the server.

        Requires the master key (admin privileges). Regular API keys will fail.
        """
        try:
            await self._request("GET", f"api/{API_VERSION}/dial/provision", {"admin_key": self.api_key})
        except VU1AuthError as err:
            raise VU1AuthError(translation_domain=DOMAIN, translation_key="provision_requires_master_key") from err


async def discover_vu1_addon(session: aiohttp.ClientSession) -> dict[str, Any]:
    """Discover the VU1 Server add-on via the Supervisor API.

    Returns ``{}`` when not on Supervisor or the add-on is not installed.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {}

    try:
        async with session.get(
            "http://supervisor/addons",
            headers={"Authorization": f"Bearer {token}"},
            timeout=ClientTimeout(total=5),
        ) as response:
            response.raise_for_status()
            addons = (await response.json())["data"]["addons"]
        matches = [addon for addon in addons if "vu-server-addon" in addon["slug"]]
    except (ClientError, TimeoutError, ValueError, LookupError, TypeError) as err:
        _LOGGER.debug("Could not list add-ons from Supervisor: %s", err)
        return {}

    if not matches:
        return {}
    running = ("started", "startup")
    addon = next((a for a in matches if a.get("state") in running), matches[0])
    # Supervisor derives an add-on's DNS hostname from its slug.
    return {
        "host": addon["slug"].replace("_", "-"),
        "port": DEFAULT_PORT,
        "running": addon.get("state") in running,
    }
