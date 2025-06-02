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
    ) -> None:
        """Initialize VU1 API client."""
        self.host = host
        self.port = port
        self.api_key = api_key
        self.ingress_slug = ingress_slug
        self.supervisor_token = supervisor_token
        
        # Set base URL based on whether we're using ingress
        if self.ingress_slug and self.supervisor_token:
            # Use internal Docker hostname for ingress-enabled add-ons
            self.base_url = f"http://local-{self.ingress_slug}:{port or DEFAULT_PORT}"
            self._use_ingress = True
        else:
            self.base_url = f"http://{host}:{port}"
            self._use_ingress = False
            
        self._session = session
        self._close_session = False

    @property
    def session(self) -> aiohttp.ClientSession:
        """Get aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=ClientTimeout(total=DEFAULT_TIMEOUT)
            )
            self._close_session = True
        return self._session

    async def close(self) -> None:
        """Close the session."""
        if self._session and self._close_session:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an API request."""
        url = f"{self.base_url}/{endpoint}"
        
        # Prepare headers and parameters
        headers = {}
        if params is None:
            params = {}
            
        # Add authentication - VU1 API key is used for both ingress and direct connections
        params["key"] = self.api_key

        try:
            async with self.session.request(method, url, params=params, headers=headers) as response:
                response.raise_for_status()  # Raises exception for 4xx/5xx status codes
                
                if response.content_type == "application/json":
                    data = await response.json()
                    
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

    async def test_connection(self) -> bool:
        """Test connection to VU1 server."""
        try:
            _LOGGER.debug("Testing connection to: %s", self.base_url)
            await self.get_dial_list()
            return True
        except VU1APIError as err:
            _LOGGER.debug("Connection test failed: %s", err)
            return False

    async def get_dial_list(self) -> List[Dict[str, Any]]:
        """Get list of available dials."""
        response = await self._request("GET", "api/v0/dial/list")
        return response.get("data", [])

    async def set_dial_value(self, dial_uid: str, value: int) -> None:
        """Set dial value (0-100)."""
        if not 0 <= value <= 100:
            raise ValueError("Value must be between 0 and 100")
        
        await self._request("GET", f"api/v0/dial/{dial_uid}/set", {"value": value})

    async def set_dial_backlight(
        self, dial_uid: str, red: int, green: int, blue: int
    ) -> None:
        """Set dial backlight RGB values (0-100 each)."""
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
        response = await self._request("GET", f"api/v0/dial/{dial_uid}/status")
        return response.get("data", {})

    async def set_dial_name(self, dial_uid: str, name: str) -> None:
        """Set dial name."""
        await self._request("GET", f"api/v0/dial/{dial_uid}/name", {"name": name})

    async def get_dial_image(self, dial_uid: str) -> bytes:
        """Get dial background image."""
        response = await self._request("GET", f"api/v0/dial/{dial_uid}/image/get")
        return response.get("data", b"")

    async def set_dial_image(self, dial_uid: str, image_data: bytes) -> None:
        """Set dial background image."""
        # This endpoint might need special handling for file uploads
        # Implementation depends on server API specifics
        _ = dial_uid, image_data  # Suppress unused parameter warnings
        raise NotImplementedError("Image upload not yet implemented")

    async def reload_dial(self, dial_uid: str) -> None:
        """Reload dial configuration."""
        await self._request("GET", f"api/v0/dial/{dial_uid}/reload")

    async def calibrate_dial(self, dial_uid: str) -> None:
        """Calibrate dial."""
        await self._request("GET", f"api/v0/dial/{dial_uid}/calibrate")

    async def set_dial_easing(self, dial_uid: str, easing_type: str) -> None:
        """Set dial easing type."""
        await self._request("GET", f"api/v0/dial/{dial_uid}/easing/dial", {"easing": easing_type})

    async def set_backlight_easing(self, dial_uid: str, easing_type: str) -> None:
        """Set backlight easing type."""
        await self._request("GET", f"api/v0/dial/{dial_uid}/easing/backlight", {"easing": easing_type})

    async def get_easing_options(self, dial_uid: str) -> Dict[str, Any]:
        """Get available easing options."""
        response = await self._request("GET", f"api/v0/dial/{dial_uid}/easing/get")
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
            # Get list of installed add-ons
            headers = {"Authorization": f"Bearer {supervisor_token}"}
            async with session.get("http://supervisor/addons", headers=headers) as response:
                if response.status != 200:
                    _LOGGER.warning("Failed to get add-ons list from Supervisor API: HTTP %s", response.status)
                    return {}
                
                data = await response.json()
                addons = data.get("data", {}).get("addons", [])
                
                _LOGGER.debug("Found %d add-ons via Supervisor API", len(addons))
                for addon in addons:
                    _LOGGER.debug("Add-on: %s (state: %s)", addon.get("slug"), addon.get("state"))
                
                # Look for VU1 Server add-on (handle different repository prefixes)
                for addon in addons:
                    addon_slug = addon.get("slug", "")
                    if "vu-server-addon" in addon_slug:
                        _LOGGER.debug("Found VU1 Server add-on: %s (state: %s)", addon_slug, addon.get("state"))
                        if addon.get("state") == "started":
                            # Found running VU1 Server add-on
                            slug = addon.get("slug", "vu-server-addon")
                            
                            # Get detailed addon info to check for ingress
                            async with session.get(f"http://supervisor/addons/{slug}/info", headers=headers) as info_response:
                                if info_response.status == 200:
                                    addon_info = await info_response.json()
                                    addon_data = addon_info.get("data", {})
                                    
                                    # Check if ingress is enabled
                                    if addon_data.get("ingress"):
                                        ingress_port = addon_data.get("ingress_port", DEFAULT_PORT)
                                        # Get the actual IP address from addon info
                                        addon_ip = addon_data.get("ip_address")
                                        _LOGGER.debug("Found VU1 Server add-on with ingress enabled on port %s, IP: %s", ingress_port, addon_ip)
                                        _LOGGER.debug("Full addon data: %s", addon_data)
                                        return {
                                            "slug": slug,
                                            "ingress": True,
                                            "ingress_port": ingress_port,
                                            "addon_ip": addon_ip,
                                            "addon_discovered": True,
                                            "supervisor_token": supervisor_token
                                        }
                                    else:
                                        # No ingress, try direct connection via hostname
                                        repo = addon.get("repository", "local")
                                        hostname = f"{repo}_{slug}".replace("_", "-")
                                        
                                        _LOGGER.debug("Found VU1 Server add-on without ingress: %s", hostname)
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


async def discover_vu1_server(host: str = "localhost", port: int = DEFAULT_PORT) -> Dict[str, Any]:
    """Discover VU1 server. Try add-on first, then fallback to direct connection."""
    _LOGGER.info("Starting VU1 server discovery...")
    
    # First try to discover via add-on
    addon_result = await discover_vu1_addon()
    if addon_result:
        _LOGGER.info("Add-on discovery returned: %s", addon_result)
        if addon_result.get("ingress"):
            # Test ingress connection using actual IP address
            addon_ip = addon_result.get("addon_ip")
            if addon_ip:
                client = VU1APIClient(
                    host=addon_ip,
                    port=addon_result.get("ingress_port", DEFAULT_PORT),
                    ingress_slug=addon_result["slug"],
                    supervisor_token=addon_result["supervisor_token"],
                    api_key=""  # Test without API key first
                )
                try:
                    # Test connection using the client's methods
                    _LOGGER.info("Testing ingress connection to %s:%s", 
                               addon_ip, addon_result.get("ingress_port", DEFAULT_PORT))
                    if await client.test_connection():
                        _LOGGER.info("VU1 Server discovered via ingress at %s:%s", 
                                   addon_ip, addon_result.get("ingress_port", DEFAULT_PORT))
                        # Update the result to use IP instead of hostname
                        addon_result["host"] = addon_ip
                        addon_result["port"] = addon_result.get("ingress_port", DEFAULT_PORT)
                        return addon_result
                    else:
                        _LOGGER.warning("VU1 Server add-on found but connection test failed")
                except Exception as err:
                    _LOGGER.warning("Ingress add-on discovered but not reachable: %s", err)
                finally:
                    await client.close()
            else:
                _LOGGER.warning("No IP address found for ingress add-on")
        else:
            # Test direct connection via hostname
            client = VU1APIClient(addon_result["host"], addon_result["port"], "")
            try:
                async with client.session.get(f"http://{addon_result['host']}:{addon_result['port']}/api/v0/dial/list") as response:
                    if response.status in [200, 401, 403]:  # Server responding
                        _LOGGER.info("VU1 Server discovered via add-on at %s:%s", addon_result["host"], addon_result["port"])
                        return addon_result
            except Exception as err:
                _LOGGER.debug("Add-on discovered but not reachable: %s", err)
            finally:
                await client.close()
    
    # Fallback to localhost discovery
    _LOGGER.info("Add-on discovery failed, trying localhost fallback...")
    client = VU1APIClient(host, port, "")
    try:
        async with client.session.get(f"http://{host}:{port}/api/v0/dial/list") as response:
            if response.status in [200, 401, 403]:  # Server responding (401/403 expected without key)
                _LOGGER.info("VU1 Server discovered at %s:%s", host, port)
                return {"host": host, "port": port, "addon_discovered": False}
            return {}
    except (ClientError, asyncio.TimeoutError):
        return {}
    except Exception:
        return {}
    finally:
        await client.close()