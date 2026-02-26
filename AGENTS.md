# Streacom VU1 Dials - Home Assistant Integration

## Project Overview

This project is a **Home Assistant Custom Component** that integrates **Streacom VU1 eInk Dials** into Home Assistant. It acts as a bridge between Home Assistant and the **VU1 Server** (which manages the physical hardware), allowing users to control dials, backlights, and background images directly from HA.

### Key Features
*   **Auto-Discovery**: Automatically finds the VU1 Server add-on if installed via Supervisor API.
*   **Sensor Binding**: Built-in logic to bind any Home Assistant sensor to a dial, mapping values (e.g., CPU Temp 30-80°C) to the dial's percentage (0-100%).
*   **Bidirectional Sync**: Renaming a dial in HA renames it on the server, and vice versa.
*   **Visual Customization**: Upload background images and control RGB backlights.
*   **Flexible Connectivity**: Supports both direct IP/Port connections and Home Assistant Ingress (for Supervisor add-ons).

---

## Architecture

### Component Structure

```
custom_components/vu1_dials/
├── __init__.py          # Entry point, runtime_data, service registration, lifecycle
├── config_flow.py       # UI config flow, options flow, and reconfigure flow handlers
├── coordinator.py       # DataUpdateCoordinator with _async_setup and retry_after
├── vu1_api.py           # Async HTTP client for VU1 Server API
├── sensor_binding.py    # Automatic sensor-to-dial binding system
├── device_config.py     # Persistent storage for dial configurations
├── diagnostics.py       # Integration diagnostics for debugging
├── const.py             # Constants, VU1DialEntity mixin, async_setup_dial_entities helper, BEHAVIOR_PRESETS
├── config_entities.py   # Configuration number/sensor entities (easing, range)
├── number.py            # Dial value number entity
├── sensor.py            # Dial status and diagnostic sensors
├── light.py             # Backlight RGB light entity
├── select.py            # Behavior preset select entity
├── button.py            # Action buttons (provision, identify, refresh)
├── image.py             # Background image entity
├── device_action.py     # Device automation triggers
├── services.yaml        # Service definitions for HA UI
├── translations/en.json # UI strings for config flow
└── manifest.json        # Integration metadata (requires HA 2025.12+)
```

### Data Flow

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Home Assistant │────▶│ VU1DataUpdate    │────▶│  VU1 Server     │
│  Entities       │◀────│ Coordinator      │◀────│  (HTTP API)     │
└─────────────────┘     └──────────────────┘     └─────────────────┘
         │                      │                        │
         │              ┌───────▼───────┐                │
         │              │ VU1APIClient  │                │
         │              └───────────────┘                │
         │                                               │
         ▼                                               ▼
┌─────────────────┐                            ┌─────────────────┐
│ VU1DialConfig   │                            │  Physical VU1   │
│ Manager (Store) │                            │  Dial Hardware  │
└─────────────────┘                            └─────────────────┘
         │
         ▼
┌─────────────────┐
│ VU1Sensor       │
│ BindingManager  │
└─────────────────┘
```

---

## Core Modules

### `__init__.py` - Integration Entry Point

**Runtime Data Pattern (HA 2024.5+):**
```python
@dataclass
class VU1RuntimeData:
    """Runtime data for VU1 Dials integration."""
    client: VU1APIClient
    coordinator: VU1DataUpdateCoordinator
    binding_manager: VU1SensorBindingManager

type VU1ConfigEntry = ConfigEntry[VU1RuntimeData]
```

Data is stored on the config entry itself via `entry.runtime_data = VU1RuntimeData(...)` instead of the legacy `hass.data[DOMAIN][entry.entry_id]` pattern. Access runtime data via `config_entry.runtime_data.coordinator`, etc.

**Key Functions:**
- `async_setup()`: Registers domain-wide services (runs once per domain)
- `async_setup_entry()`: Sets up a config entry (runs per VU1 server connection)
- `async_unload_entry()`: Cleanup when removing an entry

**Service Registration:**
Services are registered in `async_setup_services()`:
- `vu1_dials.set_dial_value` - Set dial position (0-100%)
- `vu1_dials.set_dial_backlight` - Set RGB backlight (0-100% each)
- `vu1_dials.set_dial_name` - Rename a dial
- `vu1_dials.set_dial_image` - Upload background image from media library
- `vu1_dials.reload_dial` - Reload dial hardware config
- `vu1_dials.calibrate_dial` - Hardware calibration

**Device Registry Listener:**
Handles bidirectional name sync by listening to `EVENT_DEVICE_REGISTRY_UPDATED`:
```python
@callback
def handle_device_registry_updated(event: Event[EventDeviceRegistryUpdatedData]) -> None:
    # Detect name_by_user changes and sync to VU1 server
```

### `vu1_api.py` - VU1 Server API Client

**Class: `VU1APIClient`**

Async HTTP client using `aiohttp`. Supports two authentication modes:

1. **Direct Connection**: API key passed as query parameter (`?key=...`)
2. **Ingress Mode**: Supervisor token in `Authorization` header + API key

**Key Methods:**
```python
async def test_connection() -> dict[str, Any]  # Returns {connected, authenticated, dials, error}
async def get_dial_list() -> list[dict]        # List all dials with UIDs/names
async def get_dial_status(dial_uid) -> dict    # Detailed status (value, backlight, easing, firmware)
async def set_dial_value(dial_uid, value)      # Set position 0-100
async def set_dial_backlight(dial_uid, r, g, b)  # Set RGB 0-100 each
async def set_dial_name(dial_uid, name)        # Rename dial
async def get_dial_image(dial_uid) -> bytes    # Get background PNG
async def set_dial_image(dial_uid, data, type) # Upload background (multipart POST)
async def set_dial_easing(dial_uid, period, step)      # Dial animation config
async def set_backlight_easing(dial_uid, period, step) # Backlight animation config
async def reload_dial(dial_uid)                # Refresh hardware info
async def calibrate_dial(dial_uid)             # Hardware calibration
async def provision_new_dials()                # Detect newly connected dials
```

**API Response Structure:**
```json
{
  "status": "ok",
  "data": { ... }
}
```

**Error Handling:**
Exception hierarchy for granular error handling:
- `VU1APIError` - Base exception for all API errors
- `VU1ConnectionError(VU1APIError)` - Network/connection failures (timeout, refused)
- `VU1AuthError(VU1APIError)` - Authentication failures (HTTP 401/403)

**Add-on Discovery:**
```python
async def discover_vu1_addon() -> dict[str, Any]
```
Queries Supervisor API at `http://supervisor/addons` to find VU1 Server add-on.

### `coordinator.py` - Data Update Coordinator

**Class: `VU1DataUpdateCoordinator`**

Extends `DataUpdateCoordinator` to poll VU1 server at configured intervals (default 30s).

**One-Time Setup (HA 2024.8+):**
```python
async def _async_setup(self) -> None:
    """Perform one-time setup during first refresh."""
    connection_result = await self.client.test_connection()
    if not connection_result["connected"]:
        raise UpdateFailed(...)  # Converted to ConfigEntryNotReady
```
Called automatically during `async_config_entry_first_refresh()`. Failures raise `UpdateFailed` which HA converts to `ConfigEntryNotReady`.

**Retry Backoff (HA 2025.11+):**
```python
except VU1AuthError as err:
    raise UpdateFailed(f"Auth error: {err}", retry_after=300) from err  # 5 min
except VU1APIError as err:
    raise UpdateFailed(f"API error: {err}", retry_after=60) from err    # 1 min
```
The `retry_after` parameter (float, seconds) defers the next scheduled refresh when API signals backoff conditions.

**Data Structure:**
```python
coordinator.data = {
    "dials": {
        "590056000650564139323920": {
            "uid": "590056000650564139323920",
            "dial_name": "CPU Temp",
            "image_file": "/path/to/image.png",
            "detailed_status": {
                "value": 75,
                "backlight": {"red": 100, "green": 50, "blue": 0},
                "easing": {
                    "dial_period": 50,
                    "dial_step": 5,
                    "backlight_period": 50,
                    "backlight_step": 10
                },
                "fw_version": "1.2.3",
                "hw_version": "2.0",
                "protocol_version": "1",
                "fw_hash": "abc123..."
            }
        }
    }
}
```

**Bidirectional Name Sync:**
Uses grace periods to prevent sync loops:
```python
self._name_change_grace_periods: dict[str, datetime] = {}
self._grace_period_seconds = 10
```

When a name change originates from HA:
1. `mark_name_change_from_ha()` sets grace period
2. Server updates ignored during grace period
3. Grace period expires, normal sync resumes

**Dynamic Dial Discovery:**
Supports runtime discovery of new dials via callback registration:
```python
# Register callback (returns unsubscribe function)
unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
config_entry.async_on_unload(unsub)

# Callback signature
async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
    """Create entities for newly discovered dials."""
```

Platform setup modules register callbacks to create entities when the Provision button discovers new dials. The coordinator tracks known dial UIDs and notifies callbacks only for genuinely new dials.

### `sensor_binding.py` - Automatic Sensor Binding

**Class: `VU1SensorBindingManager`**

Maps HA sensors to dial positions automatically.

**Binding Flow:**
1. User configures binding via Options Flow
2. Config stored in `device_config.py`
3. Binding manager sets up state change listeners
4. Sensor changes trigger dial updates

**Debouncing:**
Uses `Debouncer` with 5-second cooldown per dial to prevent API flooding:
```python
self._debouncers[dial_uid] = Debouncer(
    hass, _LOGGER,
    cooldown=5,
    immediate=False,
    function=functools.partial(self._apply_sensor_value, dial_uid)
)
```

**Value Mapping:**
```python
def _map_value_to_dial(self, sensor_value: float, config: dict) -> int:
    """Map sensor range to 0-100% dial range via linear interpolation."""
    value_min = config.get("value_min", 0)
    value_max = config.get("value_max", 100)
    dial_value = ((sensor_value - value_min) / (value_max - value_min)) * 100
    return max(0, min(100, int(dial_value)))
```

**Sensor Value Parsing:**
Handles various input formats:
- Direct numeric: `"23.5"` → `23.5`
- With units: `"23.5°C"` → `23.5` (regex extraction)
- Special states: `"unknown"`, `"unavailable"` → `None`

**Reference-Counted Listeners:**
State change listeners use reference counting to support multiple dials bound to the same entity:
```python
# Listener structure: entity_id -> {"unsub": callable, "count": int}
self._listeners[entity_id] = {"unsub": unsub, "count": 1}
```
When a binding is removed, the count decrements. The listener is only unsubscribed when count reaches zero.

### `device_config.py` - Persistent Configuration

**Class: `VU1DialConfigManager`**

Uses `homeassistant.helpers.storage.Store` for JSON persistence.

**Storage Location:** `.storage/vu1_dials_dial_configs`

**Config Schema:**
```python
{
    "dial_uid": {
        "bound_entity": "sensor.cpu_temperature",  # or None
        "value_min": 30.0,
        "value_max": 80.0,
        "backlight_color": [100, 50, 0],  # RGB 0-100
        "update_mode": "automatic",  # or "manual"
        "dial_easing_period": 50,
        "dial_easing_step": 5,
        "backlight_easing_period": 50,
        "backlight_easing_step": 10
    }
}
```

**Change Notification:**
Supports listener callbacks for config changes:
```python
config_manager.async_add_listener(dial_uid, callback)
config_manager.async_remove_listener(dial_uid, callback)
```

### `config_flow.py` - Configuration UI

**Type Hints (HA 2024.4+):**
```python
from homeassistant.config_entries import ConfigFlowResult  # Not FlowResult!
```

**Class: `ConfigFlow`**

Multi-step setup:
1. `async_step_user()` - Choose connection type (add-on or manual)
2. `async_step_addon()` - Enter API key for discovered add-on
3. `async_step_manual()` - Enter host, port, API key manually
4. `async_step_reconfigure()` - Change host/port/API key without removing integration

**Reconfigure Flow (HA 2024.3+):**
```python
async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
    entry = self._get_reconfigure_entry()
    # ... validate and update
    return self.async_update_reload_and_abort(entry, data_updates=updated_data)
```

**Class: `OptionsFlowHandler`**

Multi-step dial configuration:
1. `async_step_init()` - Select dial and global settings (e.g., update_interval)
2. `async_step_configure_dial()` - Choose update mode (auto/manual)
3. `async_step_configure_automatic()` - Bind sensor with value range
4. `async_step_configure_manual()` - Remove binding

**OptionsFlow Pattern (HA 2025.12 breaking change):**
```python
class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self) -> None:  # NO config_entry parameter!
        ...
    # Access via self.config_entry property (auto-provided)
```

Global options (like `update_interval`) are preserved across dial configuration steps via `_collected_options`.

### `diagnostics.py` - Integration Diagnostics

Provides debug information downloadable from Settings > Devices & Services.

```python
TO_REDACT = {"api_key", "supervisor_token", "ingress_slug"}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VU1ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator": {...},
        "dials": {...},
        "bindings": {...},
    }
```

Uses `async_redact_data()` from `homeassistant.components.diagnostics` to sanitize sensitive fields.

---

## Entity Types

### Number Entities (`number.py`, `config_entities.py`)

| Entity | Unique ID Pattern | Description |
|--------|-------------------|-------------|
| Dial Value | `vu1_dials_dial_{uid}` | Main dial position (0-100%) |
| Value Range Min | `{uid}_value_min` | Sensor binding range minimum |
| Value Range Max | `{uid}_value_max` | Sensor binding range maximum |
| Dial Easing Period | `{uid}_dial_easing_period` | Animation timing (ms) |
| Dial Easing Step | `{uid}_dial_easing_step` | Animation increment (%) |
| Backlight Easing Period | `{uid}_backlight_easing_period` | Backlight animation timing |
| Backlight Easing Step | `{uid}_backlight_easing_step` | Backlight animation increment |

### Light Entity (`light.py`)

**VU1BacklightLight**
- Color mode: RGB only
- Range conversion: HA 0-255 ↔ VU1 0-100
- On/off determined by any RGB > 0

### Sensor Entities (`sensor.py`, `config_entities.py`)

| Entity | Description |
|--------|-------------|
| VU1DialSensor | Main dial status with detailed_status attributes |
| VU1UpdateModeSensor | Shows "Manual" or "Automatic" |
| VU1BoundEntitySensor | Shows bound entity friendly name |
| VU1FirmwareVersionSensor | Diagnostic: firmware version |
| VU1HardwareVersionSensor | Diagnostic: hardware version |
| VU1ProtocolVersionSensor | Diagnostic: protocol version |
| VU1FirmwareHashSensor | Diagnostic: firmware hash |

### Select Entity (`select.py`)

**VU1BehaviorSelect**

Preset configurations:
```python
BEHAVIOR_PRESETS = {
    "responsive": {"dial_easing_period": 50, "dial_easing_step": 20, ...},
    "balanced": {"dial_easing_period": 50, "dial_easing_step": 5, ...},
    "smooth": {"dial_easing_period": 50, "dial_easing_step": 1, ...},
    "custom": {}  # When values don't match any preset
}
```

### Button Entities (`button.py`)

| Entity | Description |
|--------|-------------|
| VU1ProvisionDialsButton | Discover newly connected dials (on server device), triggers entity creation callbacks |
| VU1RefreshHardwareInfoButton | Reload dial hardware info |
| VU1IdentifyDialButton | Flash white animation to identify physical dial |

### Image Entity (`image.py`)

**VU1DialBackgroundImage**
- Fetches current dial background via API
- Caches image data to avoid repeated fetches
- Invalidates cache when `image_file` changes
- Uses `_handle_coordinator_update()` for cache invalidation on coordinator refresh

### Device Actions (`device_action.py`)

Provides automation actions for dial configuration with easing presets:
```python
EASING_PRESETS = {
    "responsive": {"dial": (50, 20), "backlight": (50, 20)},
    "balanced": {"dial": (50, 5), "backlight": (50, 10)},
    "smooth": {"dial": (50, 1), "backlight": (50, 5)},
}
```
Actions apply presets by name rather than raw period/step values.

---

## Device Hierarchy

```
VU1 Server (hub)
├── identifiers: {("vu1_dials", "vu1_server_{host}_{port}")}
└── Dial Device
    ├── identifiers: {("vu1_dials", "{dial_uid}")}
    └── via_device: ("vu1_dials", server_identifier)
```

---

## Development Conventions

### Code Style
- **Python Version**: 3.12+ (required by Home Assistant 2025.12+)
- **Type Hints**: Required for all function signatures
- **Async**: All I/O must be async. Use `hass.async_add_executor_job()` for blocking calls
- **Logging**: Use `_LOGGER = logging.getLogger(__name__)`
- **Type Aliases**: Use PEP 695 syntax: `type VU1ConfigEntry = ConfigEntry[VU1RuntimeData]`

### Entity Development Pattern
```python
class VU1ExampleEntity(VU1DialEntity, CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, dial_uid: str, dial_data: dict) -> None:
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_example"
        self._attr_name = "Example"
        self._attr_has_entity_name = True

    @property
    def native_value(self):
        return self.coordinator.data.get("dials", {}).get(self._dial_uid, {}).get("value")
```

**Notes:**
- `VU1DialEntity` mixin (from `const.py`) provides `device_info` automatically—do not define it in entity classes
- `CoordinatorEntity` provides `should_poll = False` by default—do not override unless necessary
- Always guard `coordinator.data` access (may be `None` before first refresh)

### Platform Setup Pattern
Use `async_setup_dial_entities()` from `const.py` to eliminate boilerplate:
```python
async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = config_entry.runtime_data.coordinator

    def entity_factory(dial_uid: str, dial_info: dict) -> list:
        return [VU1ExampleEntity(coordinator, dial_uid, dial_info)]

    async_setup_dial_entities(coordinator, config_entry, async_add_entities, entity_factory)
```
This handles both initial entity creation and new-dial discovery callbacks. For server-level entities (not per-dial), add them separately before calling the helper.

### Behavior Presets
`BEHAVIOR_PRESETS` is defined once in `const.py` as the canonical source. Both `select.py` and `device_action.py` import from there. Do not define preset values in multiple places.

### Adding a New Service
1. Add constant to `const.py`:
   ```python
   SERVICE_MY_ACTION = "my_action"
   ATTR_MY_PARAM = "my_param"
   ```
2. Add service handler in `__init__.py`:
   ```python
   async def my_action(call: ServiceCall) -> None:
       dial_uid = call.data[ATTR_DIAL_UID]
       await _execute_dial_service(hass, dial_uid, "my action", ...)
   ```
3. Register with schema in `async_setup_services()`
4. Add UI definition in `services.yaml`

### Error Handling Pattern

**During Setup (in coordinator._async_setup):**
```python
# Raise UpdateFailed - HA converts to ConfigEntryNotReady automatically
raise UpdateFailed(f"Cannot connect: {err}")
```

**During Runtime (in entities with UI state):**
```python
try:
    await client.some_api_call()
    # Optimistically update coordinator data — do NOT call async_request_refresh().
    # The VU1 server queues commands and applies them asynchronously (~1s),
    # so an immediate poll returns stale state and causes UI flicker.
    coordinator.data["dials"][dial_uid]["detailed_status"]["value"] = new_value
    self.async_write_ha_state()
except VU1APIError as err:
    _LOGGER.error("Failed to do thing: %s", err)
    raise HomeAssistantError(f"Failed: {err}") from err
```

**During Runtime (in services without direct UI state):**
```python
try:
    await client.some_api_call()
    await coordinator.async_request_refresh()
except VU1APIError as err:
    _LOGGER.error("Failed to do thing: %s", err)
    raise HomeAssistantError(f"Failed: {err}") from err
```

**In Coordinator Update (with retry backoff):**
```python
except VU1AuthError as err:
    raise UpdateFailed(f"Auth error: {err}", retry_after=300) from err
except VU1APIError as err:
    raise UpdateFailed(f"API error: {err}", retry_after=60) from err
```

### Optimistic Update Pattern (Entity Commands)

The VU1 server uses a queue-and-batch architecture: API responses return `"Update queued"` immediately, but hardware changes are applied during a periodic update cycle (~1s). Calling `async_request_refresh()` right after a command returns stale state and causes UI flicker (on→off→on).

**Rule:** Entity actions that change hardware state (light on/off, dial value, etc.) must use optimistic updates instead of `async_request_refresh()`:

```python
async def async_turn_off(self, **kwargs: Any) -> None:
    await self._client.set_dial_backlight(self._dial_uid, 0, 0, 0)
    # Optimistically update coordinator data in-place
    self._update_coordinator_backlight([0, 0, 0])
    self.async_write_ha_state()  # Push to UI immediately
    # The regular polling cycle (30s) will confirm actual hardware state
```

`async_request_refresh()` is still appropriate for:
- Service calls in `__init__.py` (no direct entity UI coupling)
- Name sync (`async_set_dial_name`) where server-side confirmation matters
- After `provision_new_dials()` to discover new entities

### Configuration Entity Pattern
```python
class VU1ConfigNumber(VU1ConfigEntityBase, NumberEntity):
    async def async_set_native_value(self, value: float) -> None:
        old_value = self._attr_native_value
        self._attr_native_value = value
        self.async_write_ha_state()  # Immediate UI update
        try:
            await self._update_config(my_key=value)
        except Exception:
            self._attr_native_value = old_value  # Rollback
            self.async_write_ha_state()
            raise
```

---

## VU1 Server API Reference

**Base URL**: `http://{host}:{port}` (default port: 5340)

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v0/dial/list` | List all dials |
| GET | `/api/v0/dial/{uid}/status` | Get dial status |
| GET | `/api/v0/dial/{uid}/set?value=N` | Set dial value (0-100) |
| GET | `/api/v0/dial/{uid}/backlight?red=N&green=N&blue=N` | Set backlight |
| GET | `/api/v0/dial/{uid}/name?name=X` | Set dial name |
| GET | `/api/v0/dial/{uid}/image/get` | Get background image (PNG) |
| POST | `/api/v0/dial/{uid}/image/set` | Upload background (multipart) |
| GET | `/api/v0/dial/{uid}/easing/dial?period=N&step=N` | Set dial easing |
| GET | `/api/v0/dial/{uid}/easing/backlight?period=N&step=N` | Set backlight easing |
| GET | `/api/v0/dial/{uid}/reload` | Reload dial config |
| GET | `/api/v0/dial/{uid}/calibrate?value=1024` | Calibrate dial |
| GET | `/api/v0/dial/provision` | Provision new dials |

**Authentication**: All endpoints require `?key={api_key}` parameter.

---

## Building and Testing

### Installation
1. **HACS (Recommended)**: Add as Custom Repository
2. **Manual**: Copy `custom_components/vu1_dials/` to HA config

### Development Workflow
1. Edit Python files in `custom_components/vu1_dials/`
2. Deploy to HA instance (scp, dev container, etc.)
3. Restart Home Assistant or reload integration
4. Check logs: `Settings > System > Logs` (filter by `vu1_dials`)

### Testing Checklist
- [ ] Config flow: Manual connection
- [ ] Config flow: Add-on discovery
- [ ] Config flow: Reconfigure (change host/port/API key)
- [ ] Options flow: Sensor binding
- [ ] Options flow: update_interval preserved during dial configuration
- [ ] Entity creation for all platforms
- [ ] Dial value changes update hardware
- [ ] Backlight color picker works
- [ ] Bidirectional name sync
- [ ] Image upload via media browser
- [ ] Behavior presets apply correctly
- [ ] Sensor binding auto-updates dial
- [ ] Dynamic dial discovery: Provision button creates entities for new dials
- [ ] Diagnostics: Download from Settings shows redacted sensitive data
- [ ] Error recovery: retry_after delays work on API errors

### Debugging Tips
1. Enable debug logging:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.vu1_dials: debug
   ```
2. Use Developer Tools > Services to test service calls
3. Check `.storage/vu1_dials_dial_configs` for persisted config
4. Monitor coordinator data in Developer Tools > States

---

## Key Files

| File | Purpose |
|------|---------|
| `manifest.json` | Integration metadata, version, dependencies (requires HA 2025.12+) |
| `diagnostics.py` | Debug data export with sensitive field redaction |
| `services.yaml` | Service definitions for HA UI |
| `translations/en.json` | UI strings for config/options/reconfigure flow |
| `.storage/vu1_dials_dial_configs` | Persisted dial configurations |