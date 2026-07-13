"""VU1 API Client for communicating with VU1 server."""
import asyncio
import logging
import os
import re
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientTimeout

_LOGGER = logging.getLogger(__name__)

__all__ = ["VU1APIClient", "VU1APIError", "VU1ConnectionError", "VU1AuthError", "VU1DialOfflineError", "VU1InvalidNameError", "discover_vu1_addon", "DEFAULT_PORT", "DEFAULT_TIMEOUT", "API_VERSION"]

DEFAULT_PORT = 5340
DEFAULT_TIMEOUT = 10

# Exact message the VU1 server returns (HTTP 200 + status:"fail" on dial/set and
# dial/status, HTTP 503 on setRaw/backlight/image) when a dial is offline.
OFFLINE_MESSAGE = "Invalid dial_uid or device is offline."

# Body prefix the server returns (HTTP 406) when a dial is missing on the
# name/easing/calibrate endpoints.
DEVICE_NOT_PRESENT_MESSAGE = "Device not present"


class VU1APIError(Exception):
    """Base exception for VU1 API errors."""


class VU1ConnectionError(VU1APIError):
    """Exception raised for connection/network errors."""


class VU1AuthError(VU1APIError):
    """Exception raised for authentication errors (401/403)."""


class VU1DialOfflineError(VU1APIError):
    """Exception raised when a dial is offline (HTTP 503/406)."""


class VU1InvalidNameError(VU1APIError):
    """Exception raised when a dial name fails client-side validation."""


API_VERSION = "v0"


class VU1APIClient:
    """Client for VU1 server API."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = DEFAULT_PORT,
        api_key: str = "",
        session: aiohttp.ClientSession | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize VU1 API client."""
        self.host = host
        self.port = port
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = f"http://{host}:{port}"
        self._session = session
        self._close_session = False

    def _validate_dial_uid(self, dial_uid: str) -> None:
        """Validate dial_uid parameter."""
        if not dial_uid or not isinstance(dial_uid, str):
            raise ValueError("dial_uid must be a non-empty string")

    @property
    def session(self) -> aiohttp.ClientSession:
        """Get aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=self.timeout)
            )
            self._close_session = True
        return self._session

    async def close(self) -> None:
        """Close the session."""
        if self._session and self._close_session:
            await self._session.close()
            self._session = None

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
    def _check_json_status(data: dict[str, Any]) -> None:
        """Raise the matching exception for a non-ok VU1 JSON payload.

        The server signals an offline dial with HTTP 200 + status:"fail" and the
        ``OFFLINE_MESSAGE`` body on dial/set and dial/status, so detect it here
        and surface it as ``VU1DialOfflineError`` rather than a generic error.
        """
        if data.get("status") != "ok":
            message = data.get("message", "Unknown error")
            if message == OFFLINE_MESSAGE or message.startswith(DEVICE_NOT_PRESENT_MESSAGE):
                raise VU1DialOfflineError(f"Dial offline or unavailable: {message}")
            raise VU1APIError(f"API error: {message}")

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: aiohttp.FormData | None = None,
    ) -> dict[str, Any]:
        """Make an API request."""
        url = f"{self.base_url}/{endpoint}"
        params = self._auth_params(params)

        try:
            endpoint_name = endpoint.split('/')[-1] if '/' in endpoint else endpoint
            _LOGGER.debug("Making API request to %s", endpoint_name)
            async with self.session.request(
                method,
                url,
                params=params,
                data=data,
                timeout=ClientTimeout(total=self.timeout),
            ) as response:
                _LOGGER.debug("Response status: %s", response.status)

                # Log error response body for debugging
                if response.status >= 400:
                    try:
                        error_body = await response.text()
                        _LOGGER.debug("Error response: %s", error_body[:200] + "..." if len(error_body) > 200 else error_body)
                    except Exception:
                        _LOGGER.debug("Could not read error response body")

                response.raise_for_status()

                if response.content_type == "application/json":
                    data = await response.json()

                    # Check VU1 API status field (raises on offline/error payloads)
                    self._check_json_status(data)

                    return data
                else:
                    # Handle binary responses (like images)
                    return {"data": await response.read()}
                    
        except aiohttp.ClientResponseError as err:
            self._raise_for_status(err)
        except (ClientError, asyncio.TimeoutError) as err:
            raise VU1ConnectionError(f"Connection error: {err}") from err

    @staticmethod
    def _raise_for_status(err: aiohttp.ClientResponseError) -> None:
        """Convert aiohttp response errors to VU1 exception hierarchy."""
        if err.status in (401, 403):
            raise VU1AuthError(f"Authentication failed: {err.message}") from err
        if err.status in (503, 406):
            raise VU1DialOfflineError(f"Dial offline or unavailable: {err.message}") from err
        raise VU1APIError(f"HTTP error {err.status}: {err.message}") from err

    async def test_connection(self) -> dict[str, Any]:
        """Test connection and API key, returning detailed status.

        Return contract (always a dict with these four keys):
          - ``connected``: ``False`` only on a network-level failure
            (``VU1ConnectionError`` — timeout, refused, DNS, etc.); ``True``
            whenever the server returned any HTTP response.
          - ``authenticated``: ``False`` only when the server rejected the API
            key (``VU1AuthError`` — HTTP 401/403). A generic ``VU1APIError``
            (HTTP 500 or a 200 + status:"fail" body) keeps ``authenticated``
            ``True`` and reports the problem via ``error`` — it is a server-side
            problem, not a bad key, so callers must not treat it as invalid auth.
          - ``dials``: the dial list on full success, otherwise ``[]``.
          - ``error``: ``None`` on full success, otherwise ``str(err)``.

        Callers map ``connected=False`` -> CannotConnect and
        ``authenticated=False`` -> InvalidAuth.
        """
        _LOGGER.debug("Testing connection to VU1 server at %s", self.base_url)
        try:
            # Use dial list endpoint which requires valid auth to test both connectivity and API key
            response = await self._request("GET", f"api/{API_VERSION}/dial/list")
            _LOGGER.debug("Connection and authentication successful.")
            return {
                "connected": True,
                "authenticated": True,
                "dials": response.get("data", []),
                "error": None,
            }
        except VU1ConnectionError as err:
            # Network-level connection failure (timeout, refused, etc.)
            _LOGGER.error("Connection to VU1 server failed: %s", err)
            return {
                "connected": False,
                "authenticated": False,
                "dials": [],
                "error": str(err),
            }
        except VU1AuthError as err:
            # Server responded but API key is invalid (401/403)
            _LOGGER.error("API key validation failed during connection test: %s", err)
            return {
                "connected": True,
                "authenticated": False,
                "dials": [],
                "error": str(err),
            }
        except VU1APIError as err:
            # Server responded but returned a non-auth error (HTTP 500 or a
            # 200 + status:"fail" body). This is a server-side fault, not a bad
            # API key, so keep authenticated=True and surface it via "error".
            _LOGGER.error("API error during connection test: %s", err)
            return {
                "connected": True,
                "authenticated": True,
                "dials": [],
                "error": str(err),
            }

    async def get_dial_list(self) -> list[dict[str, Any]]:
        """Get list of available dials."""
        response = await self._request("GET", f"api/{API_VERSION}/dial/list")
        return response.get("data", [])

    async def set_dial_value(self, dial_uid: str, value: int) -> None:
        """Set dial value (0-100)."""
        self._validate_dial_uid(dial_uid)
        if not 0 <= value <= 100:
            raise ValueError("Value must be between 0 and 100")
        
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/set", {"value": value})

    async def set_dial_backlight(
        self, dial_uid: str, red: int, green: int, blue: int, white: int = 0
    ) -> None:
        """Set dial backlight RGBW values (0-100 each)."""
        self._validate_dial_uid(dial_uid)
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
        self._validate_dial_uid(dial_uid)
        response = await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/status")
        return response.get("data", {})

    async def set_dial_name(self, dial_uid: str, name: str) -> None:
        """Set dial name.

        Server requires 3-30 characters, only [a-z0-9\\-_ ] allowed.
        """
        self._validate_dial_uid(dial_uid)
        if not name or not isinstance(name, str):
            raise VU1InvalidNameError("name must be a non-empty string")
        if not 3 <= len(name) <= 30:
            raise VU1InvalidNameError(f"name must be 3-30 characters, got {len(name)}")
        if not re.match(r'^[a-z0-9\-_ ]+$', name, re.IGNORECASE):
            raise VU1InvalidNameError("name may only contain letters, digits, hyphens, underscores, and spaces")
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/name", {"name": name})

    async def get_dial_image(self, dial_uid: str) -> bytes:
        """Get dial background image."""
        self._validate_dial_uid(dial_uid)
        response = await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/image/get")
        return response.get("data", b"")

    async def get_dial_image_crc(self, dial_uid: str) -> str | None:
        """Get the CRC32 of the dial's current background image."""
        self._validate_dial_uid(dial_uid)
        response = await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/image/crc")
        return response.get("data")

    async def set_dial_image(self, dial_uid: str, image_data: bytes, content_type: str = "image/png") -> None:
        """Set dial background image via multipart form upload."""
        self._validate_dial_uid(dial_uid)
        if not image_data:
            raise ValueError("image_data cannot be empty")

        form_data = aiohttp.FormData()
        form_data.add_field('imgfile', image_data, filename='background.png', content_type=content_type)

        _LOGGER.debug("Uploading image to dial %s (%d bytes)", dial_uid, len(image_data))
        await self._request("POST", f"api/{API_VERSION}/dial/{dial_uid}/image/set", data=form_data)

    async def reload_dial(self, dial_uid: str) -> None:
        """Reload dial configuration."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/reload")

    async def calibrate_dial(self, dial_uid: str, value: int = 1024) -> None:
        """Calibrate dial to specific value."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/calibrate", {"value": value})

    async def set_dial_easing(self, dial_uid: str, period: int, step: int) -> None:
        """Set dial easing configuration."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/easing/dial", {"period": period, "step": step})

    async def set_backlight_easing(self, dial_uid: str, period: int, step: int) -> None:
        """Set backlight easing configuration."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/{API_VERSION}/dial/{dial_uid}/easing/backlight", {"period": period, "step": step})


    async def provision_new_dials(self) -> dict[str, Any]:
        """Provision new dials that have been detected by the server.

        Requires the master key (admin privileges). Regular API keys will fail.
        """
        try:
            response = await self._request("GET", f"api/{API_VERSION}/dial/provision", {"admin_key": self.api_key})
            return response.get("data") or {}
        except VU1AuthError as err:
            raise VU1AuthError(
                "Provisioning requires the VU1 Server master key. "
                "The configured API key does not have admin privileges. "
                "Check your VU1 Server config.yaml for the master_key value "
                "and reconfigure the integration with it."
            ) from err


async def discover_vu1_addon() -> dict[str, Any]:
    """Discover VU1 Server add-on via Home Assistant Supervisor API."""
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.debug("No SUPERVISOR_TOKEN available, not running in Home Assistant OS")
        return {}
    
    try:
        timeout = ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {"Authorization": f"Bearer {supervisor_token}"}
            async with session.get("http://supervisor/addons", headers=headers) as response:
                if response.status != 200:
                    _LOGGER.warning("Failed to get add-ons list from Supervisor API: HTTP %s", response.status)
                    return {}
                
                data = await response.json()
                addons = data.get("data", {}).get("addons", [])
                
                _LOGGER.debug("Found %d add-ons via Supervisor API", len(addons))
                
                # Look for VU1 Server add-on (supports different repository prefixes)
                for addon in addons:
                    addon_slug = addon.get("slug", "")
                    if "vu-server-addon" in addon_slug:
                        _LOGGER.debug("Found VU1 Server add-on: %s (state: %s)", addon_slug, addon.get("state"))
                        if addon.get("state") == "started":
                            slug = addon.get("slug", "vu-server-addon")
                            
                            # Get detailed addon info for connection details
                            async with session.get(f"http://supervisor/addons/{slug}/info", headers=headers) as info_response:
                                if info_response.status == 200:
                                    addon_info = await info_response.json()
                                    addon_data = addon_info.get("data", {})

                                    # Prefer the DNS hostname over ip_address.
                                    # The hostname (e.g. "local-vu-server-addon") is
                                    # stable across reboots; the Docker IP can change.
                                    addon_host = addon_data.get("hostname") or addon_data.get("ip_address")

                                    # Connect directly to the VU1 Server API port.
                                    # The add-on's ingress proxy is for the web UI
                                    # panel only — API clients bypass it.
                                    if addon_host:
                                        _LOGGER.debug(
                                            "Found VU1 Server add-on at %s:%s",
                                            addon_host,
                                            DEFAULT_PORT,
                                        )
                                        return {
                                            "host": addon_host,
                                            "port": DEFAULT_PORT,
                                            "addon_discovered": True,
                                        }

                                    # Info call succeeded but exposed no address;
                                    # keep scanning in case another slug matches.
                                    _LOGGER.warning(
                                        "No hostname or IP found for VU1 Server add-on %s",
                                        slug,
                                    )
                                    continue
                                else:
                                    # Info lookup failed for this slug; try the next match.
                                    _LOGGER.debug("Failed to get detailed add-on info for %s", slug)
                                    continue
                        else:
                            # Matched slug isn't running; another install may be.
                            _LOGGER.debug("VU1 Server add-on %s found but not running", addon_slug)
                            continue

                _LOGGER.warning("VU1 Server add-on not found in installed add-ons")
                return {}

    except (ClientError, asyncio.TimeoutError) as err:
        _LOGGER.error("Error discovering VU1 Server add-on: %s", err)
        return {}
