"""VU1 API Client for communicating with VU1 server."""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import ClientError, ClientTimeout
import os

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 5340
DEFAULT_TIMEOUT = 10


class VU1APIError(Exception):
    """Exception raised for VU1 API errors."""


class VU1APIClient:
    """Client for VU1 server API."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = DEFAULT_PORT,
        api_key: str = "",
        session: Optional[aiohttp.ClientSession] = None,
        ingress_slug: Optional[str] = None,
        supervisor_token: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize VU1 API client."""
        self.host = host
        self.port = port
        self.api_key = api_key
        self.ingress_slug = ingress_slug
        self.supervisor_token = supervisor_token
        self.timeout = timeout
        
        self.base_url = f"http://{host}:{port}"
        # Determine if we should use ingress authentication mode
        self._use_ingress = bool(self.ingress_slug and self.supervisor_token)
            
        self._session = session
        self._close_session = False

    def _validate_dial_uid(self, dial_uid: str) -> None:
        """Validate dial_uid parameter."""
        if not dial_uid or not isinstance(dial_uid, str):
            raise ValueError("dial_uid must be a non-empty string")

    @property
    def session(self) -> aiohttp.ClientSession:
        """Get aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=self.timeout)
            )
            self._close_session = True
        return self._session

    async def close(self) -> None:
        """Close the session."""
        if self._session and self._close_session:
            await self._session.close()

    def _prepare_auth_headers_and_params(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> tuple[Dict[str, str], Dict[str, Any]]:
        """Prepare authentication headers and parameters for API requests.
        
        Args:
            endpoint: The API endpoint path
            params: Optional parameters dict to add auth to
            
        Returns:
            Tuple of (headers_dict, params_dict)
        """
        headers = {}
        if params is None:
            params = {}
            
        # For ingress mode, use supervisor token in headers
        if self._use_ingress and self.supervisor_token:
            headers["Authorization"] = f"Bearer {self.supervisor_token}"
            headers["X-Ingress-Path"] = f"/{endpoint}"
            _LOGGER.debug("Using ingress mode with supervisor token")
        
        # Always add VU1 API key as query parameter
        if self.api_key:
            params["key"] = self.api_key
            _LOGGER.debug("Using API key authentication")
            
        return headers, params

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an API request."""
        url = f"{self.base_url}/{endpoint}"
        headers, params = self._prepare_auth_headers_and_params(endpoint, params)

        try:
            endpoint_name = endpoint.split('/')[-1] if '/' in endpoint else endpoint
            _LOGGER.debug("Making API request to %s", endpoint_name)
            async with self.session.request(method, url, params=params, headers=headers) as response:
                _LOGGER.debug("Response status: %s", response.status)
                
                # Log error response body for debugging
                if response.status >= 400:
                    try:
                        error_body = await response.text()
                        _LOGGER.debug("Error response: %s", error_body[:200] + "..." if len(error_body) > 200 else error_body)
                    except:
                        _LOGGER.debug("Could not read error response body")
                
                response.raise_for_status()
                
                if response.content_type == "application/json":
                    data = await response.json()
                    
                    # Check VU1 API status field
                    if data.get("status") != "ok":
                        raise VU1APIError(f"API error: {data.get('message', 'Unknown error')}")
                    
                    return data
                else:
                    # Handle binary responses (like images)
                    return {"data": await response.read()}
                    
        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                raise VU1APIError(f"Authentication failed: Invalid API key") from err
            elif err.status == 403:
                raise VU1APIError(f"Access forbidden: Invalid API key") from err
            else:
                raise VU1APIError(f"HTTP error {err.status}: {err.message}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise VU1APIError(f"Connection error: {err}") from err

    async def test_connection(self) -> Dict[str, Any]:
        """Test connection and API key, returning detailed status."""
        _LOGGER.debug("Testing connection to VU1 server at %s", self.base_url)
        try:
            # Use dial list endpoint which requires valid auth to test both connectivity and API key
            response = await self._request("GET", "api/v0/dial/list")
            _LOGGER.debug("Connection and authentication successful.")
            return {
                "connected": True,
                "authenticated": True,
                "dials": response.get("data", []),
                "error": None,
            }
        except VU1APIError as err:
            # Server responded but API key is invalid
            _LOGGER.error("API key validation failed during connection test: %s", err)
            return {
                "connected": True,
                "authenticated": False,
                "dials": [],
                "error": str(err),
            }
        except (ClientError, asyncio.TimeoutError) as err:
            # Network-level connection failure
            _LOGGER.error("Connection to VU1 server failed: %s", err)
            return {
                "connected": False,
                "authenticated": False,
                "dials": [],
                "error": f"Connection error: {err}",
            }

    async def get_dial_list(self) -> List[Dict[str, Any]]:
        """Get list of available dials."""
        response = await self._request("GET", "api/v0/dial/list")
        return response.get("data", [])

    async def set_dial_value(self, dial_uid: str, value: int) -> None:
        """Set dial value (0-100)."""
        self._validate_dial_uid(dial_uid)
        if not 0 <= value <= 100:
            raise ValueError("Value must be between 0 and 100")
        
        await self._request("GET", f"api/v0/dial/{dial_uid}/set", {"value": value})

    async def set_dial_backlight(
        self, dial_uid: str, red: int, green: int, blue: int
    ) -> None:
        """Set dial backlight RGB values (0-100 each)."""
        self._validate_dial_uid(dial_uid)
        for color, val in [("red", red), ("green", green), ("blue", blue)]:
            if not 0 <= val <= 100:
                raise ValueError(f"{color} value must be between 0 and 100")
        
        await self._request(
            "GET",
            f"api/v0/dial/{dial_uid}/backlight",
            {"red": red, "green": green, "blue": blue},
        )

    async def get_dial_status(self, dial_uid: str) -> Dict[str, Any]:
        """Get dial status."""
        self._validate_dial_uid(dial_uid)
        response = await self._request("GET", f"api/v0/dial/{dial_uid}/status")
        return response.get("data", {})

    async def set_dial_name(self, dial_uid: str, name: str) -> None:
        """Set dial name."""
        self._validate_dial_uid(dial_uid)
        if not name or not isinstance(name, str):
            raise ValueError("name must be a non-empty string")
        await self._request("GET", f"api/v0/dial/{dial_uid}/name", {"name": name})

    async def get_dial_image(self, dial_uid: str) -> bytes:
        """Get dial background image."""
        self._validate_dial_uid(dial_uid)
        response = await self._request("GET", f"api/v0/dial/{dial_uid}/image/get")
        return response.get("data", b"")


    async def reload_dial(self, dial_uid: str) -> None:
        """Reload dial configuration."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/v0/dial/{dial_uid}/reload")

    async def calibrate_dial(self, dial_uid: str, value: int = 1024) -> None:
        """Calibrate dial to specific value."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/v0/dial/{dial_uid}/calibrate", {"value": value})

    async def set_dial_easing(self, dial_uid: str, period: int, step: int) -> None:
        """Set dial easing configuration."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/v0/dial/{dial_uid}/easing/dial", {"period": period, "step": step})

    async def set_backlight_easing(self, dial_uid: str, period: int, step: int) -> None:
        """Set backlight easing configuration."""
        self._validate_dial_uid(dial_uid)
        await self._request("GET", f"api/v0/dial/{dial_uid}/easing/backlight", {"period": period, "step": step})


    async def provision_new_dials(self) -> Dict[str, Any]:
        """Provision new dials that have been detected by the server."""
        response = await self._request("GET", "api/v0/dial/provision")
        return response.get("data", {})


async def discover_vu1_addon() -> Dict[str, Any]:
    """Discover VU1 Server add-on via Home Assistant Supervisor API."""
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.warning("No SUPERVISOR_TOKEN available, not running in Home Assistant OS")
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
                            
                            # Get detailed addon info to check ingress configuration
                            async with session.get(f"http://supervisor/addons/{slug}/info", headers=headers) as info_response:
                                if info_response.status == 200:
                                    addon_info = await info_response.json()
                                    addon_data = addon_info.get("data", {})
                                    
                                    # Check if ingress is enabled
                                    if addon_data.get("ingress"):
                                        ingress_port = addon_data.get("ingress_port", DEFAULT_PORT)
                                        addon_ip = addon_data.get("ip_address")
                                        _LOGGER.debug("Found VU1 Server add-on with ingress enabled on port %s", ingress_port)
                                        return {
                                            "slug": slug,
                                            "ingress": True,
                                            "ingress_port": ingress_port,
                                            "addon_ip": addon_ip,
                                            "addon_discovered": True,
                                            "supervisor_token": supervisor_token
                                        }
                                    else:
                                        # No ingress, use direct IP connection
                                        addon_ip = addon_data.get("ip_address")
                                        if addon_ip:
                                            _LOGGER.debug("Found VU1 Server add-on without ingress, using IP: %s", addon_ip)
                                            return {
                                                "host": addon_ip,
                                                "port": DEFAULT_PORT,
                                                "addon_discovered": True
                                            }
                                        else:
                                            # Fallback: construct hostname (less reliable)
                                            repo = addon.get("repository", "local")
                                            hostname = f"{repo}_{slug}".replace("_", "-")
                                            _LOGGER.warning(
                                                "No IP address found for add-on, falling back to constructed hostname: %s", 
                                                hostname
                                            )
                                            return {
                                                "host": hostname,
                                                "port": DEFAULT_PORT,
                                                "addon_discovered": True
                                            }
                                else:
                                    _LOGGER.debug("Failed to get detailed add-on info")
                                    return {}
                        else:
                            _LOGGER.debug("VU1 Server add-on found but not running")
                            return {}
                
                _LOGGER.warning("VU1 Server add-on not found in installed add-ons")
                return {}
                
    except Exception as err:
        _LOGGER.error("Error discovering VU1 Server add-on: %s", err)
        return {}


