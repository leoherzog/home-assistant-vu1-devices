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
        
        # Set base URL - always use provided host and port
        self.base_url = f"http://{host}:{port}"
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
            
        # Handle ingress authentication differently
        if self._use_ingress and self.supervisor_token:
            # For ingress mode, add Supervisor token to headers instead of URL params
            headers["Authorization"] = f"Bearer {self.supervisor_token}"
            headers["X-Ingress-Path"] = f"/{endpoint}"
            _LOGGER.debug("Using ingress mode with supervisor token")
        
        # Add VU1 API key authentication 
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
        
        # Use centralized authentication logic
        headers, params = self._prepare_auth_headers_and_params(endpoint, params)

        try:
            endpoint_name = endpoint.split('/')[-1] if '/' in endpoint else endpoint
            _LOGGER.debug("Making API request to %s", endpoint_name)
            async with self.session.request(method, url, params=params, headers=headers) as response:
                _LOGGER.debug("Response status: %s", response.status)
                
                # Log response body for error cases to help debug
                if response.status >= 400:
                    try:
                        error_body = await response.text()
                        _LOGGER.debug("Error response: %s", error_body[:200] + "..." if len(error_body) > 200 else error_body)
                    except:
                        _LOGGER.debug("Could not read error response body")
                
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

    async def test_connection(self) -> Dict[str, Any]:
        """Test connection to VU1 server and validate API key.
        
        Returns:
            Dict containing:
            - connected: bool - Whether server is reachable
            - authenticated: bool - Whether API key is valid
            - dials: List[Dict] - Available dials (if authenticated)
            - error: str - Error message (if any)
        """
        try:
            _LOGGER.debug("Testing connection to: %s", self.base_url)
            
            # Test with API key if available
            if self.api_key:
                try:
                    response = await self._request("GET", "api/v0/dial/list")
                    _LOGGER.debug("Connection and API key validation successful")
                    return {
                        "connected": True,
                        "authenticated": True,
                        "dials": response.get("data", []),
                        "error": None
                    }
                except VU1APIError as err:
                    # API key validation failed, but server is reachable
                    _LOGGER.debug("Server reachable but API key invalid: %s", err)
                    return {
                        "connected": True,
                        "authenticated": False,
                        "dials": [],
                        "error": str(err)
                    }
            else:
                # No API key provided, just test connectivity
                url = f"{self.base_url}/api/v0/dial/list"
                async with self.session.get(url) as response:
                    # Server is reachable if we get any HTTP response
                    if response.status in [200, 401, 403]:
                        _LOGGER.debug("Server is reachable (HTTP %s)", response.status)
                        return {
                            "connected": True,
                            "authenticated": False,
                            "dials": [],
                            "error": "No API key provided" if response.status in [401, 403] else None
                        }
                    else:
                        _LOGGER.debug("Server returned unexpected status: %s", response.status)
                        return {
                            "connected": False,
                            "authenticated": False,
                            "dials": [],
                            "error": f"Server returned HTTP {response.status}"
                        }
                        
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Connection test failed: %s", err)
            return {
                "connected": False,
                "authenticated": False,
                "dials": [],
                "error": f"Connection failed: {err}"
            }
        except Exception as err:
            _LOGGER.error("Unexpected error during connection test: %s", err)
            return {
                "connected": False,
                "authenticated": False,
                "dials": [],
                "error": f"Unexpected error: {err}"
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

    async def set_dial_image(self, dial_uid: str, image_data: bytes) -> None:
        """Set dial background image."""
        self._validate_dial_uid(dial_uid)
        
        # Create multipart form data
        data = aiohttp.FormData()
        data.add_field('imgfile', image_data, filename='background.png', content_type='image/png')
        
        endpoint = f"api/v0/dial/{dial_uid}/image/set"
        url = f"{self.base_url}/{endpoint}"
        
        # Use centralized authentication logic
        headers, params = self._prepare_auth_headers_and_params(endpoint)
        
        try:
            _LOGGER.debug("Uploading image for dial %s (%d bytes)", dial_uid, len(image_data))
            async with self.session.post(url, data=data, headers=headers, params=params) as response:
                _LOGGER.debug("Image upload response status: %s", response.status)
                
                if response.status >= 400:
                    try:
                        error_body = await response.text()
                        _LOGGER.error("Image upload failed: %s", error_body[:200] + "..." if len(error_body) > 200 else error_body)
                    except:
                        _LOGGER.error("Image upload failed with status %s", response.status)
                    
                response.raise_for_status()
                
                if response.content_type == "application/json":
                    result = await response.json()
                    if result.get("status") != "ok":
                        raise VU1APIError(f"Failed to set image: {result.get('message', 'Unknown error')}")
                    _LOGGER.info("Successfully uploaded image for dial %s", dial_uid)
                    
        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                raise VU1APIError(f"Authentication failed: Invalid API key") from err
            elif err.status == 403:
                raise VU1APIError(f"Access forbidden: Invalid API key") from err
            else:
                raise VU1APIError(f"HTTP error {err.status}: {err.message}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise VU1APIError(f"Connection error during image upload: {err}") from err
        except Exception as err:
            raise VU1APIError(f"Failed to upload image: {err}") from err

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

    async def get_easing_options(self, dial_uid: str) -> Dict[str, Any]:
        """Get available easing options."""
        self._validate_dial_uid(dial_uid)
        response = await self._request("GET", f"api/v0/dial/{dial_uid}/easing/get")
        return response.get("data", {})

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
            # Get list of installed add-ons
            headers = {"Authorization": f"Bearer {supervisor_token}"}
            async with session.get("http://supervisor/addons", headers=headers) as response:
                if response.status != 200:
                    _LOGGER.warning("Failed to get add-ons list from Supervisor API: HTTP %s", response.status)
                    return {}
                
                data = await response.json()
                addons = data.get("data", {}).get("addons", [])
                
                _LOGGER.debug("Found %d add-ons via Supervisor API", len(addons))
                
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
                                        # No ingress, use the IP address provided by Supervisor API
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


async def discover_vu1_server(host: str = "localhost", port: int = DEFAULT_PORT) -> Dict[str, Any]:
    """Discover VU1 server. Try add-on first, then fallback to direct connection."""
    _LOGGER.debug("Starting VU1 server discovery...")
    
    # First try to discover via add-on
    addon_result = await discover_vu1_addon()
    if addon_result:
        _LOGGER.debug("Add-on discovery found VU1 server addon")
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
                    _LOGGER.debug("Testing ingress connection to %s:%s", 
                               addon_ip, addon_result.get("ingress_port", DEFAULT_PORT))
                    connection_result = await client.test_connection()
                    if connection_result["connected"]:
                        _LOGGER.info("VU1 Server discovered via ingress")
                        # Update the result to use IP instead of hostname
                        addon_result["host"] = addon_ip
                        addon_result["port"] = addon_result.get("ingress_port", DEFAULT_PORT)
                        return addon_result
                    else:
                        _LOGGER.warning("VU1 Server add-on found but connection test failed: %s", 
                                      connection_result["error"])
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
                        _LOGGER.info("VU1 Server discovered via add-on")
                        return addon_result
            except Exception as err:
                _LOGGER.debug("Add-on discovered but not reachable: %s", err)
            finally:
                await client.close()
    
    # Fallback to localhost discovery
    _LOGGER.debug("Add-on discovery failed, trying localhost fallback...")
    client = VU1APIClient(host, port, "")
    try:
        async with client.session.get(f"http://{host}:{port}/api/v0/dial/list") as response:
            if response.status in [200, 401, 403]:  # Server responding (401/403 expected without key)
                _LOGGER.info("VU1 Server discovered at localhost")
                return {"host": host, "port": port, "addon_discovered": False}
            return {}
    except (ClientError, asyncio.TimeoutError):
        return {}
    except Exception:
        return {}
    finally:
        await client.close()