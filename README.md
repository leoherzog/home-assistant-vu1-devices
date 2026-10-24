# Streacom VU1 Dials

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg?logo=HomeAssistantCommunityStore&logoColor=white)](https://github.com/hacs/integration)

A Home Assistant integration for [Streacom VU1](https://streacom.com/products/vu1-dynamic-analogue-dials/) eInk dials. It talks to the VU1 Server HTTP API to set needle positions, RGBW backlights and background images, and can drive a dial from a numeric sensor, input_number, number or counter entity.

## Features

- **App or manual setup** - the VU1 Server app is offered when installed; otherwise enter host, port and key
- **Entity binding** - drive a dial from a sensor, input_number, number or counter, mapped from a range you choose to 0-100%
- **Name sync** between Home Assistant and the VU1 Server (see [Known limitations](#known-limitations))
- **RGBW backlight** as a light entity
- **Background images** from the media library or an upload
- **Behavior presets** - Responsive, Balanced or Smooth needle and backlight easing
- **Dial provisioning** - add newly connected dials from Home Assistant

## Requirements

- Home Assistant 2026.8.0 or newer
- A VU1 Server (app or standalone) reachable from Home Assistant. A standalone server binds to `hostname:` in its `config.yaml`, which ships as `localhost`; set it empty (all interfaces) or to a LAN address.
- The server's master key or an API key

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=leoherzog&repository=home-assistant-vu1-devices&category=integration)

1. Add this repository as a custom repository in HACS
2. Install "Streacom VU1 Dials"
3. Restart Home Assistant

### Manual

1. Copy `custom_components/vu1_dials` to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings** → **Devices & services** → **Add integration** and search for "Streacom VU1 Dials"
2. Choose a connection method:
   - **VU1 Server app** - offered when the app is installed. Host and port are filled in; you only enter the key.
   - **Manual** - enter host, port (default 5340) and key. A standalone server must not be bound to `localhost` (see [Requirements](#requirements)).

One VU1 Server can be added per Home Assistant instance.

### Where to get the key

1. Open the VU1 Server Web UI (**Open Web UI** on the app page, or browse to the server).
2. Either use the **master key**, or create a key under **API Keys** in the Web UI's top bar. An API key only sees the dials granted to it; grants can be edited later under **API Keys**, and newly added dials must be granted. Provisioning new dials needs the master key.

The app uses VU-Server's default master key, and the Web UI has it built in. The server prints it on every start (`Provide '<key>' to your main application.`, at the default `info` log level), so restart the app and read its log if you need it. Because the default key is public, anything that can reach port 5340 has admin access to the server.

If the key is later rejected, Home Assistant asks for a new one (re-authentication). To change host, port or key, use **Reconfigure** on the integration.

## Entities

Each dial is a device under the VU1 Server device. Example entity IDs assume a dial named "CPU Dial".

| Platform | Name | Category | Description |
|----------|------|----------|-------------|
| Number | *(device name)*, e.g. `number.cpu_dial` | | Needle position, 0-100% |
| Light | Backlight, e.g. `light.cpu_dial_backlight` | | RGBW backlight |
| Image | Background image, e.g. `image.cpu_dial_background_image` | | Current dial face |
| Sensor | Update mode | | Automatic or Manual |
| Sensor | Bound entity | | Bound entity ID; unknown in manual mode |
| Select | Dial behavior | Configuration | Responsive, Balanced or Smooth; unknown when the easing matches no preset |
| Number | Value range minimum / maximum | Configuration | Source values shown as 0% and 100% |
| Number | Dial easing period / step | Configuration | Needle movement (ms / %), default 50 / 2 |
| Number | Backlight easing period / step | Configuration | Backlight transition (ms / %), default 100 / 5 |
| Button | Identify | Diagnostic | Flashes the backlight white, then restores it |
| Button | Refresh hardware info | Diagnostic | Re-reads firmware and hardware info from the dial |
| Sensor | Server name | Diagnostic | The dial name stored on the VU1 Server |
| Sensor | Protocol version, Firmware hash | Diagnostic | Disabled by default |

The VU1 Server device has one entity:

| Platform | Name | Category | Description |
|----------|------|----------|-------------|
| Button | Provision new dials | Configuration | Adds dials newly connected to the server (needs the master key) |

## Options

**Settings** → **Devices & services** → **Streacom VU1 Dials** → **Configure**:

- **Update interval** - how often the server is polled (default 30 seconds, 5-300)
- **Request timeout** - default 10 seconds, 1-60
- **Configure dial** (optional) - pick a dial, then:
  - **Update mode** - *Automatic* binds an entity and a value range; *Manual* removes the binding
  - **Background image** - upload a PNG or JPEG up to 2 MB. It is converted to greyscale and fitted to the 200x144 display (width x height) on a white background.

Changing the update interval or timeout reloads the integration.

### Entity binding

In automatic mode the dial follows the bound entity. The value range minimum maps to 0% and the maximum to 100%; values outside the range are clamped. The state must be a number, optionally followed by a unit (`23.5 °C`). State changes are applied at once, then at most one per dial every 5 seconds; changing the binding or range applies the current state immediately. Each poll also re-sends the bound value when the dial does not show it, so a server restart or an outside change is corrected within one update interval. Setting a dial's value from Home Assistant (number entity or `set_dial_value`) switches it to manual.

## Actions

Every action takes a target: dial entities, dial devices, areas, floors or labels. An area, floor or label reaches a dial only through its visible entities that are not configuration or diagnostic entities (value, backlight, background image, update mode, bound entity). A dial whose entities were moved to another area or hidden is not targeted by its device's area.

| Action | Field | Required | Description |
|--------|-------|----------|-------------|
| `vu1_dials.set_dial_value` | `value` | yes | Needle position, 0-100. A dial in automatic mode is switched to manual. |
| `vu1_dials.set_dial_backlight` | `red`, `green`, `blue` | yes | 0-100 each |
| | `white` | no | 0-100; the current white level is kept when omitted |
| `vu1_dials.set_dial_name` | `name` | yes | 3-30 characters: letters, digits, space, `-` and `_`. Exactly one dial may be targeted. |
| `vu1_dials.set_dial_image` | `media_content_id` | yes | A local media library image: PNG or JPEG up to 2 MB, converted to greyscale and fitted to 200x144 |
| `vu1_dials.reload_dial` | | | No fields. Re-reads hardware info, like the Refresh hardware info button. |

### Device action: Configure dial

In the automation editor, a dial device offers **Configure dial**. Every field is optional and only the fields you set are changed:

| Field | Description |
|-------|-------------|
| `bound_entity` | Entity to bind |
| `value_min`, `value_max` | Range mapped to 0-100%; the minimum must be less than the maximum |
| `backlight_color` | RGB colour, 0-255 per channel; the white level is kept |
| `dial_easing`, `backlight_easing` | `responsive`, `balanced` or `smooth` |
| `update_mode` | `automatic` or `manual` |

### Examples

```yaml
actions:
  - action: number.set_value
    target:
      entity_id: number.cpu_dial
    data:
      value: 75

  - action: light.turn_on
    target:
      entity_id: light.cpu_dial_backlight
    data:
      rgbw_color: [255, 128, 0, 0]
      brightness: 200

  - action: vu1_dials.set_dial_backlight
    target:
      device_id: 0123456789abcdef0123456789abcdef
    data:
      red: 100
      green: 50
      blue: 0

  - action: vu1_dials.set_dial_image
    target:
      entity_id: image.cpu_dial_background_image
    data:
      media_content_id: "media-source://media_source/local/dial_backgrounds/cpu.png"

  - device_id: 0123456789abcdef0123456789abcdef
    domain: vu1_dials
    type: configure_dial
    bound_entity: sensor.cpu_temperature
    value_min: 30
    value_max: 90
    update_mode: automatic
```

## Data updates

Each update interval the integration reads the dial list, then each dial's status and image checksum. Changes made from Home Assistant show at once; changes made on the server (names, easing, images) show on the next poll. The checksum is skipped for a dial with no image. A server name of "Not set" is ignored and the device is named `VU1 Dial <uid>`; firmware and hardware versions of "?" are left empty.

## Known limitations

- One VU1 Server per Home Assistant instance.
- Provisioning new dials requires the master key. Provisioning resets every dial. After **Provision new dials** in Home Assistant, the previous value of each manual dial is re-sent, and automatic dials and backlights are restored by the refresh that follows. Provisioning from the VU1 Server Web UI or at server start restores only automatic dials and backlights, and an automatic dial whose bound value maps to 0 is not re-sent.
- Name sync has limits. Renaming a dial device in Home Assistant sends the name to the server only if it is 3-30 characters of letters, digits, space, `-` and `_`; otherwise the name stays in Home Assistant and a repair issue explains why. Once you rename the device in Home Assistant, that name wins: later renames on the server no longer change it. The `vu1_dials.set_dial_name` action instead rejects an invalid name with an error and keeps following server renames.
- The VU1 Server does not keep backlight colours across restarts or provisioning. Home Assistant stores the last colour it set and re-applies it whenever a poll finds the backlight off. A backlight turned off outside Home Assistant is turned back on; turn it off from Home Assistant instead.
- The dial value is the last value sent to the server, not a reading from the hardware. A manual dial's value is not restored after a server restart, because a reset 0 cannot be told apart from a 0 you set.
- A dial device can only be deleted once the server no longer reports the dial. The server keeps listing an unplugged dial until it rescans the bus: press **Provision new dials** or restart the server. With no dials left connected, only a restart clears it. While the integration is not loaded this check is skipped: the dial's stored settings are deleted and it returns with defaults on the next setup. The VU1 Server device cannot be deleted.

## Removal

1. **Settings** → **Devices & services** → **Streacom VU1 Dials** → **⋮** → **Delete**. This removes the devices, entities and the stored per-dial settings (bindings, ranges, backlight colours).
2. If installed with HACS, remove it in HACS; if installed manually, delete `custom_components/vu1_dials`. Restart Home Assistant.

The VU1 Server and its dial names, images and easing are left unchanged.

## Troubleshooting

### Dial not responding
1. Check the VU1 Server is running and reachable
2. Press **Refresh hardware info**; it fails with an offline error when the dial does not answer
3. Check the Home Assistant logs for `vu1_dials`

When the server gives up on a dial after repeated failed writes, a repair issue says the dial is not responding. Its entities stay available: check the dial's connection, then set a new value or colour, or press **Provision new dials**, to make the server retry. The issue closes on the next poll after the dial responds.

### Access denied
The API key is not granted that dial. Grant it under **API Keys** in the VU1 Server Web UI. A rejected key instead starts re-authentication.

### Binding not updating
1. Check the update mode is Automatic
2. Check the bound entity exists and has a numeric state
3. Check the value range covers the entity's values

## Support

- [GitHub Issues](https://github.com/leoherzog/home-assistant-vu1-devices/issues)

### License

Feel free to take a look at the source and adapt as you please. Streacom VU1 Dials is licensed under the [MIT License](LICENSE).

---

#### About Me

<a href="https://herzog.tech/" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/link-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/link.svg.png">
    <img src="https://herzog.tech/signature/link.svg.png" width="32px">
  </picture>
</a>
<a href="https://mastodon.social/@herzog" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/mastodon-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/mastodon.svg.png">
    <img src="https://herzog.tech/signature/mastodon.svg.png" width="32px">
  </picture>
</a>
<a href="https://github.com/leoherzog" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/github-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/github.svg.png">
    <img src="https://herzog.tech/signature/github.svg.png" width="32px">
  </picture>
</a>
<a href="https://keybase.io/leoherzog" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/keybase-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/keybase.svg.png">
    <img src="https://herzog.tech/signature/keybase.svg.png" width="32px">
  </picture>
</a>
<a href="https://www.linkedin.com/in/leoherzog" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/linkedin-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/linkedin.svg.png">
    <img src="https://herzog.tech/signature/linkedin.svg.png" width="32px">
  </picture>
</a>
<a href="https://hope.edu/directory/people/herzog-leo/" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/anchor-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/anchor.svg.png">
    <img src="https://herzog.tech/signature/anchor.svg.png" width="32px">
  </picture>
</a>
<br />
<a href="https://herzog.tech/$" target="_blank">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://herzog.tech/signature/mug-tea-saucer-solid-light.svg.png">
    <source media="(prefers-color-scheme: light)" srcset="https://herzog.tech/signature/mug-tea-saucer-solid.svg.png">
    <img src="https://herzog.tech/signature/mug-tea-saucer-solid.svg.png" alt="Buy Me A Tea" width="32px">
  </picture>
  Found this helpful? Buy me a tea!
</a>
