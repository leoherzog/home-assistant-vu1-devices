# Streacom VU1 Dials - Home Assistant Integration

## Project Overview

This project is a **Home Assistant Custom Component** that integrates **Streacom VU1 eInk Dials** into Home Assistant. It acts as a bridge between Home Assistant and the **VU1 Server** (which manages the physical hardware), allowing users to control dials, backlights, and background images directly from HA.

### Key Features
*   **Auto-Discovery**: Automatically finds the VU1 Server add-on if installed via Supervisor API.
*   **Sensor Binding**: Built-in logic to bind any Home Assistant sensor to a dial, mapping values (e.g., CPU Temp 30-80°C) to the dial's percentage (0-100%).
*   **Bidirectional Sync**: Renaming a dial in HA renames it on the server, and vice versa.
*   **Visual Customization**: Upload background images and control RGB backlights.
*   **Connectivity**: Connects directly to the VU1 Server's HTTP API at `host:port` (default `5340`). When running under Supervisor, the add-on's `host:port` is auto-discovered; the API key is always entered manually.

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
├── device_action.py     # Device automation actions
├── services.yaml        # Service definitions for HA UI
├── strings.json         # Source UI strings (config/options flow + entity names)
├── translations/en.json # English translations (kept byte-identical to strings.json)
├── icons.json           # Per-entity icons keyed by translation_key
└── manifest.json        # Integration metadata (integration_type: hub)
```

> Minimum Home Assistant version (`2025.12.0`) lives in `hacs.json`, **not** in
> `manifest.json` — there is no `homeassistant` key in the manifest.

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
Services are registered **once per HA session** in `async_setup_services()` (called
from `async_setup`, guarded by a `hass.services.has_service(...)` check):
- `vu1_dials.set_dial_value` - Set dial position (0-100%)
- `vu1_dials.set_dial_backlight` - Set RGB backlight (0-100% each)
- `vu1_dials.set_dial_name` - Rename a dial
- `vu1_dials.set_dial_image` - Set background image from a media-source URI
- `vu1_dials.reload_dial` - Reload dial hardware config
- `vu1_dials.calibrate_dial` - Hardware calibration

**Services are intentionally NOT unregistered in `async_unload_entry`.** They must
survive config-entry reloads (reconfigure, options save, `OptionsFlowWithReload`,
manual reload). The handlers resolve the target dial across all loaded entries and
raise `ServiceValidationError` when no entry/dial is available. Service targets are
resolved from `device_id` / `entity_id` / `area_id` / `floor_id` / `label_id`
selectors via `_resolve_dial_uids_from_call()`; most services fan out across all
targeted dials concurrently (`set_dial_name` is single-target only).

**Device Registry Listener:**
Handles bidirectional name sync by listening to `EVENT_DEVICE_REGISTRY_UPDATED`:
```python
@callback
def handle_device_registry_updated(event: Event[EventDeviceRegistryUpdatedData]) -> None:
    # Detect name_by_user changes and sync to VU1 server
```

### `vu1_api.py` - VU1 Server API Client

**Class: `VU1APIClient`**

Async HTTP client using `aiohttp`. There is exactly **one** authentication mode:
the configured API key is appended as the `?key=...` query parameter on every
request (via `_auth_params()`; admin endpoints like `provision` send it as
`admin_key` instead). There is **no ingress/Supervisor-token auth path** — the
Supervisor token is used only by the separate `discover_vu1_addon()` helper to
look up the add-on's host, never to authenticate API calls.

The constructor takes a `timeout` (default `DEFAULT_TIMEOUT = 10s`); it is applied
**per request** via `ClientTimeout(total=self.timeout)` (and on the session). The
integration passes the entry's `timeout` option in, so changing the option and
reloading changes the request timeout.

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
- `VU1DialOfflineError(VU1APIError)` - Dial offline/unavailable. The server signals
  this as **HTTP 503** on some endpoints **and** as **HTTP 200 + `status:"fail"`**
  with the body `"Invalid dial_uid or device is offline."` on `dial/set` and
  `dial/status`. `_check_json_status()` inspects the JSON `status`/`message` of
  every 200 response and raises this rather than a generic error.

**`test_connection()` contract** (always returns a dict with these four keys):
- `connected`: `False` **only** on a network-level failure (`VU1ConnectionError`);
  `True` whenever the server returned any HTTP response.
- `authenticated`: `False` **only** when the key was rejected (`VU1AuthError`,
  401/403). A generic `VU1APIError` (HTTP 500 or a 200 + `status:"fail"`) keeps
  `authenticated=True` and reports via `error` — it's a server fault, not a bad key.
- `dials`: the dial list on full success, else `[]`.
- `error`: `None` on full success, else `str(err)`.

Callers map `connected=False` → `CannotConnect` and `authenticated=False` →
`InvalidAuth` (config flow) / `ConfigEntryAuthFailed` (coordinator).

**Add-on Discovery:**
```python
async def discover_vu1_addon() -> dict[str, Any]
```
Queries the Supervisor API at `http://supervisor/addons` (Bearer `SUPERVISOR_TOKEN`)
to find the `vu-server-addon` slug, then reads `/addons/{slug}/info` and returns the
add-on's stable DNS **`hostname`** (falling back to `ip_address`) plus port `5340`.
Returns `{}` when there is no Supervisor token or no running add-on.

### `coordinator.py` - Data Update Coordinator

**Class: `VU1DataUpdateCoordinator`**

Extends `DataUpdateCoordinator` to poll VU1 server at configured intervals (default 30s).

**One-Time Setup (HA 2024.8+):**
```python
async def _async_setup(self) -> None:
    """Perform one-time setup during first refresh."""
    connection_result = await self.client.test_connection()
    if not connection_result["connected"]:
        raise UpdateFailed(...)              # -> ConfigEntryNotReady (retry)
    if not connection_result["authenticated"]:
        raise ConfigEntryAuthFailed(...)     # -> starts a reauth flow
```
Called automatically during `async_config_entry_first_refresh()`. A connection
failure raises `UpdateFailed` (HA converts to `ConfigEntryNotReady`); a rejected
key raises `ConfigEntryAuthFailed` so HA starts a reauth flow instead of retrying
forever with a dead key.

**Update-loop error mapping (`_async_update_data`):**
```python
except VU1AuthError as err:
    raise ConfigEntryAuthFailed(f"Authentication error: {err}") from err  # reauth
except VU1ConnectionError as err:
    raise UpdateFailed(f"Connection error: {err}") from err               # standard retry
except VU1APIError as err:
    raise UpdateFailed(f"API error: {err}", retry_after=60) from err      # 1 min backoff
```
Auth failures during polling now raise `ConfigEntryAuthFailed` (triggering reauth),
**not** `UpdateFailed(retry_after=300)`. Only generic API errors use `retry_after`
(60s); connection errors use the coordinator's standard retry.

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

Flow:
1. A rename in the HA UI fires `EVENT_DEVICE_REGISTRY_UPDATED`; the `__init__.py`
   listener calls the **public** `coordinator.async_handle_ha_name_change(dial_uid, name)`,
   which pushes the name to the server via `async_set_dial_name()`.
2. `async_set_dial_name()` calls `mark_name_change_from_ha()` to open a grace period.
3. The grace period is **only consulted in `_sync_name_from_server()`** (the
   server→HA echo path) to drop the echo of HA's own change. `async_handle_ha_name_change`
   deliberately does **no** grace check — it relies on a `_previous_dial_names`
   comparison instead, so a second user rename inside the window still syncs.

The integration never writes `name_by_user`, so there is no server→HA→server loop
beyond the single echo the grace period suppresses.

**Dynamic Dial Discovery:**
New dials are detected **inside `_async_update_data` on every poll** by diffing the
freshly fetched UID set against `self._known_dial_uids`. Genuinely new UIDs schedule
`async_notify_new_dials()` (run as a task so it executes after `self.data` is
populated), which invokes registered callbacks. This covers dials provisioned
**outside** HA (e.g. via the server web UI), not just the Provision button — the
button simply forces a refresh.

Platforms register an entity-creation callback via the `async_setup_dial_entities()`
helper in `const.py`:
```python
unsub = coordinator.register_new_dial_callback(async_add_new_dial_entities)
config_entry.async_on_unload(unsub)

async def async_add_new_dial_entities(new_dials: dict[str, Any]) -> None:
    """Create entities for newly discovered dials."""
```

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

**Class: `ConfigFlow`** (`VERSION = 3`)

Steps:
1. `async_step_user()` - Choose connection type. `discover_vu1_addon()` runs first;
   if the add-on is found an "VU1 Server Add-on" option is offered alongside "Manual".
2. `async_step_addon()` - Enter **only the API key**; host/port come from discovery.
3. `async_step_manual()` - Enter host, port, API key manually.
4. `async_step_reconfigure()` - Change host/port/API key without removing the integration
   (re-discovers the add-on host for add-on-managed entries).
5. `async_step_reauth()` / `async_step_reauth_confirm()` - Triggered when the API key
   is rejected (`ConfigEntryAuthFailed`); prompts for a new key and updates the entry.

Duplicate prevention uses `_async_abort_entries_match` (on host/port for manual, on
`addon_managed` for the add-on entry), **not** a host-based `unique_id`.
`async_migrate_entry` handles v1→v2 (drop legacy ingress fields, fix port) and
v2→v3 (re-key the hub device + clear the stale host-based `unique_id`; see below).

**Reconfigure Flow (HA 2024.3+):**
```python
async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
    entry = self._get_reconfigure_entry()
    # ... validate and update
    return self.async_update_reload_and_abort(entry, data_updates=updated_data)
```

**Class: `OptionsFlowHandler`**

Multi-step flow:
1. `async_step_init()` - Global options (`update_interval`, `timeout`) + optional dial picker
2. `async_step_configure_dial()` - Choose `update_mode` or `upload_image`
3. `async_step_configure_update_mode()` - Choose automatic/manual
4. `async_step_configure_automatic()` - Bind a sensor with a value range
5. `async_step_configure_manual()` - Remove binding
6. `async_step_upload_image()` - Upload a background image (file selector)

**OptionsFlow Pattern:**
```python
class OptionsFlowHandler(config_entries.OptionsFlowWithReload):
    def __init__(self) -> None:  # NO config_entry parameter!
        ...
    # Access via self.config_entry property (auto-provided)
```

Subclasses **`OptionsFlowWithReload`** (not plain `OptionsFlow`): the base class
reloads the entry on save, so changed `update_interval`/`timeout` take effect
immediately (the coordinator + client are re-created with the new values in
`async_setup_entry`). Both `update_interval` **and** `timeout` are real, effective
options — `update_interval` drives `coordinator.update_interval`; `timeout` is
passed into `VU1APIClient`. The `__init__` takes **no** `config_entry` argument
(HA 2025.12 breaking change); both options are preserved across the dial
sub-steps via `_collected_options`.

### `diagnostics.py` - Integration Diagnostics

Provides debug information downloadable from Settings > Devices & Services.

```python
TO_REDACT = {"api_key"}

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
├── identifiers: {("vu1_dials", "vu1_server_{entry_id}")}
└── Dial Device
    ├── identifiers: {("vu1_dials", "{dial_uid}")}
    └── via_device: ("vu1_dials", "vu1_server_{entry_id}")
```

The hub identifier is keyed on the **config entry id**, not `host:port`. Earlier
versions keyed it on `host:port`, which churned (and orphaned the hub device) when
the add-on's Docker IP / DNS hostname changed. The `v2→v3` migration in
`async_migrate_entry` rewrites the existing hub device's identifier to
`vu1_server_{entry_id}` (keeping the same `device.id` so child dials stay linked
via `via_device`) and clears the now-unused host-based config-entry `unique_id`
(duplicate prevention moved to `_async_abort_entries_match`). Dial devices are
still identified by raw `dial_uid`. The `"vu1_server_"` prefix is how hub-vs-dial
devices are distinguished throughout the code (e.g. in service target resolution
and `async_remove_config_entry_device`).

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
    _attr_translation_key = "example"  # name comes from strings.json/en.json

    def __init__(self, coordinator, dial_uid: str, dial_data: dict) -> None:
        super().__init__(coordinator)
        self._dial_uid = dial_uid
        self._attr_unique_id = f"{dial_uid}_example"
        # _attr_has_entity_name = True is inherited from VU1DialEntity

    @property
    def native_value(self):
        return self.coordinator.data.get("dials", {}).get(self._dial_uid, {}).get("value")
```

**Notes:**
- `VU1DialEntity` mixin (from `const.py`) provides `device_info` automatically and
  sets `_attr_has_entity_name = True`—do not redefine either in entity classes.
- **Entity names come from translation keys**, not hard-coded `_attr_name` strings.
  Each entity sets `_attr_translation_key` (or, for `EntityDescription`-based
  entities, `translation_key=`), and the display name lives under
  `entity.<platform>.<key>.name` in `strings.json`/`translations/en.json`. Icons
  live in `icons.json` keyed by the same translation key.
- The **main dial-value number** is the device's primary feature, so it sets
  `self._attr_name = None` (inherits the device name with no suffix) rather than a
  translation key.
- `CoordinatorEntity` provides `should_poll = False` by default—do not override unless necessary.
- Always guard `coordinator.data` access (may be `None` before first refresh).

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

**In Coordinator Update:**
```python
except VU1AuthError as err:
    # Bad/revoked key -> start a reauth flow, don't back off forever
    raise ConfigEntryAuthFailed(f"Authentication error: {err}") from err
except VU1ConnectionError as err:
    raise UpdateFailed(f"Connection error: {err}") from err
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
| `manifest.json` | Integration metadata: `integration_type: hub`, `dependencies: ["file_upload"]`, `after_dependencies: ["device_automation"]`, `iot_class: local_polling`. **No `homeassistant` key** — the minimum HA version (`2025.12.0`) is in `hacs.json`. |
| `hacs.json` | HACS metadata; holds the minimum HA version (`2025.12.0`). |
| `strings.json` | Source UI strings (config/options flow + entity names). |
| `translations/en.json` | English translations; kept **byte-identical** to `strings.json`. |
| `icons.json` | Per-entity icons keyed by `translation_key`. |
| `diagnostics.py` | Debug data export with sensitive field redaction. |
| `services.yaml` | Service definitions for HA UI. |
| `.storage/vu1_dials_dial_configs` | Persisted dial configurations. |