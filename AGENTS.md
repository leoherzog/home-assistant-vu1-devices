# Streacom VU1 Dials - Agent Notes

Home Assistant custom integration `vu1_dials` (hub, `local_polling`, config entries only) for Streacom VU1 eInk dials. It drives dials through the VU1 Server HTTP API. There is no hardware access from HA and no test suite in this repo.

- `manifest.json`: `single_config_entry: true` (one VU1 Server per HA), `dependencies: ["file_upload"]`. The minimum HA version (2026.9.0) is in `hacs.json`.
- Target HA 2026.9, Python 3.14.

## Layout

```
custom_components/vu1_dials/
├── __init__.py          # Setup/unload/remove, runtime_data, services, device-registry rename listener
├── config_flow.py       # Config flow (add-on / manual), reconfigure, reauth, options flow
├── coordinator.py       # VU1DataUpdateCoordinator, name/easing/hardware sync, backlight write path
├── entity.py            # VU1DialEntity base, get_dial_device_info, async_setup_dial_entities
├── vu1_api.py           # VU1APIClient, exception hierarchy, discover_vu1_addon
├── device_config.py     # VU1DialConfigManager (per-dial settings in a Store)
├── sensor_binding.py    # VU1SensorBindingManager (entity -> dial value)
├── config_entities.py   # VU1ConfigEntityBase, config numbers, update-mode and bound-entity sensors
├── number.py light.py select.py sensor.py button.py image.py
├── device_action.py     # "Configure dial" device action
├── diagnostics.py
├── const.py             # Keys, service/attr names, HassKeys, BEHAVIOR_PRESETS
├── services.yaml strings.json translations/en.json icons.json manifest.json
```

`strings.json` and `translations/en.json` are kept byte-identical. Entity names, exception and issue texts and selector states are translated; service names, descriptions and field names live in the `services` block; `services.yaml` holds only targets, fields and selectors; icons live in `icons.json`.

## Lifecycle (`__init__.py`)

- `async_setup`: loads `VU1DialConfigManager` into `hass.data[DATA_CONFIG_MANAGER]`, creates `VU1SensorBindingManager` in `hass.data[DATA_BINDING_MANAGER]`, registers services. Managers and services live for the whole HA session and survive entry reloads.
- `async_setup_entry`: builds the client (HA's shared aiohttp session, `timeout` option) and coordinator (`update_interval` option), creates the hub device `(DOMAIN, "vu1_server_{entry_id}")` and stores its id as `coordinator.hub_device_id`, runs the first refresh, sets `entry.runtime_data = VU1RuntimeData(client, coordinator, binding_manager)`, listens for device-registry `name_by_user` changes, forwards platforms, applies bindings.
- `async_unload_entry`: unloads platforms and removes bindings for the entry's dials.
- `async_remove_entry`: clears and deletes the dial-config store.
- `async_remove_config_entry_device`: the hub is never removable; a dial only once the server no longer reports it, and its stored config is removed with it.

The `vu1_server_` identifier prefix is how hub and dial devices are told apart everywhere (service targets, device actions, rename listener). Dial devices use the raw dial UID.

## API client (`vu1_api.py`)

`VU1APIClient(host, port, api_key, session, timeout)`. Every request goes through `_request`, which adds `key=<api_key>` unless the call passes `admin_key`, and parses the `{status, message, data}` envelope. Methods:

```
get_dial_list() -> list[dict]
get_dial_status(uid) -> dict
get_dial_image_crc(uid) -> str | None
get_dial_image(uid) -> bytes
set_dial_value(uid, value)                       # 0-100
set_dial_backlight(uid, red, green, blue, white) # 0-100 each, white required
set_dial_name(uid, name)                         # validates 3-30 of [A-Za-z0-9 _-]
set_dial_image(uid, image_data)                  # PNG/JPEG magic bytes, <= 2 MB, multipart field imgfile
set_dial_easing(uid, period, step)
set_backlight_easing(uid, period, step)
reload_dial(uid)                                 # data:false -> dial_not_found
provision_new_dials()                            # admin_key; VU1AuthError -> provision_requires_master_key
```

Errors: `VU1APIError(HomeAssistantError)` carries translation keys, so entity actions and services let it propagate unwrapped. Subclasses: `VU1AuthError` (401/403), `VU1InvalidNameError` and `VU1InvalidImageError` (both also `ServiceValidationError`). Everything else is a plain `VU1APIError` whose translation key tells the cases apart: `cannot_connect` (aiohttp error/timeout), `dial_offline` (503/406, or 200 + `status:"fail"` with the offline message), `not_vu1_server` (non-JSON body), `api_error`.

`discover_vu1_addon(session)`: with `SUPERVISOR_TOKEN`, lists Supervisor add-ons, picks a slug containing `vu-server-addon` and returns `{host: slug with _ -> -, port: 5340, running}`, else `{}`.

## Coordinator (`coordinator.py`)

`VU1DataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]])`, `always_update=False`, `server_device_identifier = "vu1_server_{entry_id}"`.

```python
coordinator.data = {"dials": {uid: {
    **dial_list_item,          # uid, dial_name, value, backlight, image_file ("img_<uid>" or "img_blank"), ...
    "detailed_status": {...},  # /status data; {} when that call failed
    "image_crc": "A1B2C3D4",   # or None when that call failed
}}}
```

Each poll: dial list, then status and image CRC for every dial concurrently. A failed status is logged at info once per outage. For a dial with status it runs `_sync_device_registry` (server name, `sw_version`, `hw_version`), `_check_server_behavior_change` (server easing copied into the config store) and `_async_restore_backlight`, then `binding_manager.async_update_bindings`. An empty dial list creates the repair issue `no_dials`; any dial deletes it (so does unloading the entry). `VU1AuthError` raises `ConfigEntryAuthFailed` (reauth); any other `VU1APIError` raises `UpdateFailed` (`ConfigEntryNotReady` on the first refresh).

- **Backlight**: `async_set_backlight(uid, red, green, blue, white=None)` is the HA write path (light, service, device action). It sends all four channels (`white=None` keeps the stored white), persists `backlight_color`, writes the value into `detailed_status` and calls `async_update_listeners()`. The server resets backlight to 0 on restart but keeps easing, so `_async_restore_backlight` re-applies the stored colour on every poll where the server reports all channels 0 and the stored red, green or blue is non-zero (a white-only colour is not restored, since the server strips white from its reports). The server may omit `white`; readers fall back to the stored white. The identify button flashes white through the client for 1 s, then restores the previous colour through `async_set_backlight`.
- **Name sync**: an HA rename (`name_by_user` change) calls `async_handle_ha_name_change`, which calls `async_set_dial_name` (server rename, device `name` update, 10 s grace period). `VU1InvalidNameError` creates the repair issue `invalid_dial_name_<uid>`; the next HA rename deletes it. Server to HA: `_sync_device_registry` updates the device `name` unless the device has `name_by_user` or is in its grace period, so a user-set HA name always wins.
- **Easing**: HA-originated easing changes call `mark_behavior_change_from_ha` (10 s grace) so the next poll does not overwrite them with stale server values.
- **Commands are optimistic**: entity actions and bindings write the new value into coordinator data in place instead of calling `async_request_refresh()`. Services refresh after the call, except `set_dial_backlight`: `/dial/list` strips `white` from the server's live backlight dict, so a list call before the server flushes a queued backlight write (200 ms) breaks that write. The device action does not refresh either.
- `_get_dial_client_and_coordinator(hass, uid)` finds the loaded entry that reports a dial.

## Entities

All dial entities subclass `VU1DialEntity` (`entity.py`): `_attr_has_entity_name = True`, device info from `get_dial_device_info` (name, model, `sw_version`, `hw_version`, `serial_number`, `via_device_id` = `coordinator.hub_device_id`), a `dial_data` property and availability tied to the dial being in coordinator data. Each platform's `async_setup_entry` calls the sync `@callback async_setup_dial_entities(entry, async_add_entities, factory)`; it adds entities for current dials and, through a coordinator listener, for dials that appear later (deleting a dial device reloads the entry, which re-adds them).

| Platform | Class | unique_id | translation_key | Category / notes |
|----------|-------|-----------|-----------------|------------------|
| number | `VU1DialNumber` | `vu1_dials_dial_{uid}` | `dial_value` (icon only) | `_attr_name = None`, so the entity id is the device slug. Setting it switches automatic to manual. |
| number | `VU1ConfigNumber` x6 | `{uid}_{key}` | `value_min`, `value_max`, `dial_easing_period`, `dial_easing_step`, `backlight_easing_period`, `backlight_easing_step` | config. Easing numbers write to the server first, then the store. Min/max raise `value_min_not_less_than_max`. |
| light | `VU1BacklightLight` | `{uid}_backlight` | `backlight` | RGBW only; HA 0-255 scaled to device 0-100; on when any channel > 0 |
| select | `VU1BehaviorSelect` | `{uid}_behavior_preset` | `behavior_preset` | config. Options `responsive`/`balanced`/`smooth`; `None` when the stored easing matches none |
| image | `VU1DialBackgroundImage` | `{uid}_background_image` | `background_image` | Cached bytes dropped when `image_file` or `image_crc` changes |
| sensor | `VU1UpdateModeSensor` | `{uid}_update_mode_status` | `update_mode` | enum `automatic`/`manual` |
| sensor | `VU1BoundEntitySensor` | `{uid}_bound_entity_status` | `bound_entity` | Bound entity_id, `None` in manual mode |
| sensor | `VU1ServerNameSensor` | `{uid}_server_name` | `server_name` | diagnostic, enabled |
| sensor | `VU1DiagnosticSensorBase` x2 | `{uid}_{protocol_version,fw_hash}` | `protocol_version`, `firmware_hash` | diagnostic, disabled by default (firmware and hardware versions are on the device) |
| button | `VU1RefreshHardwareInfoButton` | `{uid}_refresh_hardware_info` | `refresh_hardware_info` | diagnostic; `reload_dial` then refresh |
| button | `VU1IdentifyDialButton` | `{uid}_identify` | (device class `identify`) | diagnostic; white flash for 1 s, then restore |
| button | `VU1ProvisionDialsButton` | `vu1_server_{entry_id}_provision_new_dials` | `provision_new_dials` | config, on the hub device; provision then `async_refresh()` |

`PARALLEL_UPDATES`: sensor 0, image 0, number/light/select/button 1.

`VU1ConfigEntityBase` (config numbers, select, the two config sensors) subscribes to config-manager changes and holds `_apply_easing_config_to_server`.

## Dial config store (`device_config.py`)

`.storage/vu1_dials_dial_configs`, version 1, `{"dial_configs": {uid: config}}`, saved with a 10 s delay. Unknown keys are dropped on load and update.

```python
{
    "bound_entity": None,             # entity_id or None
    "value_min": 0.0, "value_max": 100.0,  # swapped if min > max
    "backlight_color": [0, 0, 0, 0],  # RGBW 0-100; all zero = never set
    "update_mode": "manual",          # or "automatic"
    "dial_easing_period": 50, "dial_easing_step": 5,
    "backlight_easing_period": 50, "backlight_easing_step": 5,
}
```

API: `get_dial_config(uid)` (a copy, defaults when unknown), `async_update_dial_config(uid, partial)` (merge, validate, schedule save, await listeners), `async_remove_dial_config(uid)`, `async_add_listener(uid, async_cb)` / `async_remove_listener`. Access it with `async_get_config_manager(hass)`.

## Entity binding (`sensor_binding.py`)

`VU1SensorBindingManager`, accessed with `async_get_binding_manager(hass)`. A binding exists while `update_mode == "automatic"` and `bound_entity` is set. One state listener per source entity, reference-counted across dials. A per-dial `Debouncer(cooldown=5, immediate=True)` applies the first change at once and then at most one update per 5 s. New bindings and changes to `BINDING_KEYS` (`bound_entity`, `update_mode`, `value_min`, `value_max`) apply the current state immediately.

- Parsing: the state must fully match a number with an optional trailing non-digit unit (`"23.5 °C"`); anything else is ignored.
- Mapping: linear to 0-100, clamped at the range ends, rounded; returns 50 when min == max. A mapped value equal to the dial's `detailed_status` value in coordinator data is not sent; otherwise it is sent and written into coordinator data, so a server restart or an outside change is corrected on the next state change.
- After changing binding config call `async_reconfigure_dial_binding(uid)`.

## Services (`__init__.py`)

Registered once in `async_setup`. Each schema is `vol.Schema({**cv.TARGET_SERVICE_FIELDS, ...})`; `services.yaml` targets `entity: integration: vu1_dials` (hassfest rejects device filters on targets) and its names/descriptions live in the `services` block of `strings.json`; `_resolve_dial_uids_from_call` maps every resolved entity (explicit, or via device/area/floor/label) to its registry device, adds only the explicitly targeted device ids, keeps the dial devices and raises `no_dials_targeted` when none match; an explicit entity counts as missing only when it is not in the state machine.

| Service | Fields |
|---------|--------|
| `set_dial_value` | `value` 0-100 (switches automatic dials to manual) |
| `set_dial_backlight` | `red`, `green`, `blue` 0-100; optional `white` 0-100 (kept when omitted) |
| `set_dial_name` | `name`; single target only (`single_dial_only`) |
| `set_dial_image` | `media_content_id` (media selector dict or URI string); must resolve to a local file |
| `reload_dial` | none |

`_execute_dial_service_for_all(hass, uids, action_name, api_call, refresh=True)` runs `api_call(coordinator, uid)` for every dial concurrently, refreshes the coordinator once if `refresh` and any call succeeded, and raises one `action_failed` error listing failures (`ServiceValidationError` if every failure was one).

Adding a service: add `SERVICE_*`/`ATTR_*` to `const.py`; write a handler that calls `_resolve_dial_uids_from_call(hass, call)` and `_execute_dial_service_for_all(hass, uids, "my action", lambda coordinator, uid: coordinator.client.my_call(uid, ...))`; register it with `vol.Schema({**cv.TARGET_SERVICE_FIELDS, vol.Required(ATTR_MY_PARAM): ...})`; add its target/fields/selectors to `services.yaml`, its name/description/field names to the `services` block of `strings.json` and `translations/en.json`, and it to the `services` block of `icons.json`.

## Device action (`device_action.py`)

`configure_dial`, offered for dial devices. Optional fields: `bound_entity`, `value_min`, `value_max` (merged with the stored config; `value_min_not_less_than_max` when min >= max), `backlight_color` (RGB 0-255 selector, sent through `async_set_backlight` keeping the stored white), `dial_easing` and `backlight_easing` (preset slugs from `BEHAVIOR_PRESETS`), `update_mode`. Only fields present are applied.

`BEHAVIOR_PRESETS` in `const.py` is the one source for presets (select and device action): each has the four easing values and a `description`.

## Config flow (`config_flow.py`)

- `user`: runs `discover_vu1_addon`; shows a menu (`addon`, `manual`) when found, otherwise goes to `manual`.
- `addon`: aborts `addon_not_running` if the add-on is stopped; asks only for the key.
- `manual`: host, port, key.
- `reconfigure`: host, port, optional key (empty keeps the current one).
- `reauth` / `reauth_confirm`: new key.
- `validate_input` calls `get_dial_list()` and returns `invalid_auth`, `cannot_connect` or `unknown`; a server with no dials is accepted so the provision button stays reachable (the `no_dials` repair issue explains it).

`OptionsFlowHandler(OptionsFlowWithReload)`: `init` (`update_interval` 5-300, `timeout` 1-60, optional dial picker) → `configure_dial` menu → `configure_update_mode` → `configure_automatic` (entity picker for sensor/input_number/number/counter plus range) or `configure_manual`; or `upload_image` (file_upload, sent with `set_dial_image`). Every exit saves the existing options merged with `update_interval`/`timeout`; the entry reloads only when those options changed.

## Diagnostics

Keys: `config_entry` (`entry_id`, `domain`, redacted `title`, `data` and `options` with `api_key` and `host` redacted), `coordinator` (`last_update_success`, `update_interval`, `server_device_identifier`), `dials` (`dial_name`, `image_file`, `detailed_status`), `dial_configs`, `sensor_bindings` (from `async_get_bindings_summary()`).

## VU1 Server API

Base `http://{host}:{port}/api/v0`, default port 5340. Responses are `{"status": "ok"|"fail", "message": ..., "data": ...}`; a 200 can still be `fail`.

| Method | Endpoint | Auth | Params / notes |
|--------|----------|------|----------------|
| GET | `/dial/list` | `key` | Dials the key may access; `backlight` has `white` removed |
| GET | `/dial/{uid}/status` | none | Full dial dict |
| GET | `/dial/{uid}/image/crc` | none | 8-char hex CRC32, `"00000000"` when no file |
| GET | `/dial/{uid}/image/get` | none | Image bytes |
| POST | `/dial/{uid}/image/set` | `key` | multipart `imgfile` |
| GET | `/dial/{uid}/set` | `key` | `value` |
| GET | `/dial/{uid}/backlight` | `key` | `red`, `green`, `blue`, `white` (0-100) |
| GET | `/dial/{uid}/name` | `key` | `name`, 3-30 of `[A-Za-z0-9 _-]` |
| GET | `/dial/{uid}/easing/dial` | `key` | `period`, `step` |
| GET | `/dial/{uid}/easing/backlight` | `key` | `period`, `step` |
| GET | `/dial/{uid}/reload` | `key` | `data: false` for an unknown dial |
| GET | `/dial/provision` | `admin_key` | Master key (level 99) only |

The client sends `key` on the unauthenticated endpoints too. The server persists names and easing but not backlight. `value` is the last commanded value, not a hardware readback. The add-on's master key is printed on every server start (`Provide '<key>' to your main application.`).

## Working on this repo

- Keep changes small; prefer deleting code. No migration or compatibility code, no comments narrating a change, no try/except that only logs and re-raises.
- Import and lint gate:
  ```
  uv run --no-project --python 3.14 --with homeassistant==2026.9.2 python -c "import importlib,pkgutil,custom_components.vu1_dials as p;[importlib.import_module('custom_components.vu1_dials.'+m.name) for m in pkgutil.iter_modules(p.__path__)];print('import ok')" && uvx ruff check custom_components/vu1_dials
  ```
- Line endings are mixed per file (`git ls-files --eol`); preserve them. CRLF: `__init__.py`, `button.py`, `config_flow.py`, `const.py`, `coordinator.py`, `diagnostics.py`, `image.py`, `light.py`, `select.py`, `sensor.py`, `vu1_api.py`, `services.yaml`, `strings.json`, `translations/en.json`, `hacs.json`, `README.md`. Everything else is LF.
- Edit `strings.json` and `translations/en.json` together.
- CI (`.github/workflows/hacs.yml`) runs the HACS action and hassfest on push, pull request, daily and on demand.
- Debug logging: `logger: logs: custom_components.vu1_dials: debug`. Persisted settings are in `.storage/vu1_dials_dial_configs`.
- `developers.home-assistant/` is a local copy of the HA developer docs for reference.
