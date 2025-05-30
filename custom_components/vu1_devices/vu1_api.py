"""VU1 API Client for communicating with VU1 server."""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import ClientError, ClientTimeout

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
    ) -> None:
        """Initialize VU1 API client."""
        self.host = host
        self.port = port
        self.api_key = api_key
        self.base_url = f"http://{host}:{port}"
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
        
        # Add API key to parameters
        if params is None:
            params = {}
        params["key"] = self.api_key

        try:
            async with self.session.request(method, url, params=params) as response:
                if response.content_type == "application/json":
                    data = await response.json()
                    
                    if data.get("status") != "ok":
                        raise VU1APIError(f"API error: {data.get('message', 'Unknown error')}")
                    
                    return data
                else:
                    # Handle binary responses (like images)
                    return {"data": await response.read()}
                    
        except ClientError as err:
            raise VU1APIError(f"Connection error: {err}") from err

    async def test_connection(self) -> bool:
        """Test connection to VU1 server."""
        try:
            await self.get_dial_list()
            return True
        except VU1APIError:
            return False

    async def get_dial_list(self) -> List[Dict[str, Any]]:
        """Get list of available dials."""
        response = await self._request("GET", "dial/list")
        return response.get("data", [])

    async def set_dial_value(self, dial_uid: str, value: int) -> None:
        """Set dial value (0-100)."""
        if not 0 <= value <= 100:
            raise ValueError("Value must be between 0 and 100")
        
        await self._request("GET", f"dial/{dial_uid}/set", {"value": value})

    async def set_dial_backlight(
        self, dial_uid: str, red: int, green: int, blue: int
    ) -> None:
        """Set dial backlight RGB values (0-100 each)."""
        for color, val in [("red", red), ("green", green), ("blue", blue)]:
            if not 0 <= val <= 100:
                raise ValueError(f"{color} value must be between 0 and 100")
        
        await self._request(
            "GET",
            f"dial/{dial_uid}/backlight",
            {"red": red, "green": green, "blue": blue},
        )

    async def get_dial_status(self, dial_uid: str) -> Dict[str, Any]:
        """Get dial status."""
        response = await self._request("GET", f"dial/{dial_uid}/status")
        return response.get("data", {})

    async def set_dial_name(self, dial_uid: str, name: str) -> None:
        """Set dial name."""
        await self._request("GET", f"dial/{dial_uid}/name", {"name": name})

    async def get_dial_image(self, dial_uid: str) -> bytes:
        """Get dial background image."""
        response = await self._request("GET", f"dial/{dial_uid}/image/get")
        return response.get("data", b"")

    async def set_dial_image(self, dial_uid: str, image_data: bytes) -> None:
        """Set dial background image."""
        # This endpoint might need special handling for file uploads
        # Implementation depends on server API specifics
        raise NotImplementedError("Image upload not yet implemented")

    async def reload_dial(self, dial_uid: str) -> None:
        """Reload dial configuration."""
        await self._request("GET", f"dial/{dial_uid}/reload")

    async def calibrate_dial(self, dial_uid: str) -> None:
        """Calibrate dial."""
        await self._request("GET", f"dial/{dial_uid}/calibrate")

    async def set_dial_easing(self, dial_uid: str, easing_type: str) -> None:
        """Set dial easing type."""
        await self._request("GET", f"dial/{dial_uid}/easing/dial", {"easing": easing_type})

    async def set_backlight_easing(self, dial_uid: str, easing_type: str) -> None:
        """Set backlight easing type."""
        await self._request("GET", f"dial/{dial_uid}/easing/backlight", {"easing": easing_type})

    async def get_easing_options(self, dial_uid: str) -> Dict[str, Any]:
        """Get available easing options."""
        response = await self._request("GET", f"dial/{dial_uid}/easing/get")
        return response.get("data", {})


async def discover_vu1_server(host: str = "localhost", port: int = DEFAULT_PORT) -> bool:
    """Discover VU1 server on given host and port."""
    client = VU1APIClient(host, port, "")
    try:
        # Try to connect without API key first
        async with client.session.get(f"http://{host}:{port}/dial/list") as response:
            return response.status in [200, 401, 403]  # Server responding
    except Exception:
        return False
    finally:
        await client.close()