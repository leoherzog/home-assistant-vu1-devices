# Streacom VU1 Dials

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg?logo=HomeAssistantCommunityStore&logoColor=white)](https://github.com/hacs/integration)

A Home Assistant integration for [Streacom VU1](https://streacom.com/products/vu1-dynamic-analogue-dials/) eInk dials. Control dial values, RGB backlighting, background images, and automatic sensor binding through Home Assistant.

## Features

- **Auto-discovery** of VU1 Server add-on or manual configuration
- **Automatic sensor binding** - bind any HA sensor to a dial with configurable value mapping
- **Bidirectional name sync** - rename dials in HA or VU1 server, changes sync automatically
- **RGB backlight control** - use HA's native color picker via light entity
- **Background image support** - set dial backgrounds from HA media library
- **Behavior presets** - Responsive, Balanced, Smooth, or Custom easing settings
- **Dial provisioning** - discover and add new dials from the server

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=leoherzog&repository=home-assistant-vu1-devices&category=integration)

1. Add this repository as a custom repository in HACS
2. Install "Streacom VU1 Dials"
3. Restart Home Assistant

### Manual

1. Copy `custom_components/vu1_dials` to your HA `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for "Streacom VU1 Dials"
3. Choose connection method:
   - **Add-on**: Auto-discovered if VU1 Server add-on is installed
   - **Manual**: Enter host, port (default: 5340), and API key

## Entities

Each dial creates the following entities:

| Platform | Entity | Description |
|----------|--------|-------------|
| Sensor | Dial Value | Current dial position (0-100%) |
| Sensor | Update Mode | Current mode (Automatic/Manual) |
| Sensor | Bound Entity | Entity ID bound for automatic updates |
| Number | Dial Value | Control dial position directly |
| Light | Backlight | RGB backlight with color picker |
| Image | Background | Current dial background image |
| Select | Behavior | Dial movement preset |
| Button | Identify | Flash the dial for identification |
| Button | Refresh | Reload hardware info from dial |

Additionally, the server device includes:

| Platform | Entity | Description |
|----------|--------|-------------|
| Button | Provision Dials | Discover and add new dials |

### Configuration Entities

These entities appear under the dial device for advanced configuration:

- **Value Min/Max** - Map sensor range to dial range (e.g., 0-100°C → 0-100%)
- **Dial Easing Period/Step** - Fine-tune needle movement speed
- **Backlight Easing Period/Step** - Fine-tune backlight transition speed

### Diagnostic Entities (disabled by default)

- Firmware Version
- Hardware Version
- Protocol Version
- Firmware Hash

## Configuration

### Automatic Sensor Binding

1. Go to **Devices & Services** → **VU1 Dials** → **Configure**
2. Select a dial to configure
3. Set **Update Mode** to "Automatic"
4. Choose a sensor entity to bind
5. Set value min/max to map the sensor range to dial percentage

The integration automatically updates the dial when the bound sensor changes, with debouncing to prevent excessive updates.

### Manual Control

Control dials directly via the number entity or services:

```yaml
# Set dial value via number entity
service: number.set_value
target:
  entity_id: number.cpu_dial_value
data:
  value: 75

# Set RGB backlight via light entity
service: light.turn_on
target:
  entity_id: light.cpu_dial_backlight
data:
  rgb_color: [255, 128, 0]
  brightness: 200
```

## Services

| Service | Description |
|---------|-------------|
| `vu1_dials.set_dial_value` | Set dial needle position (0-100%) |
| `vu1_dials.set_dial_backlight` | Set RGB backlight color (0-100% per channel) |
| `vu1_dials.set_dial_name` | Rename a dial |
| `vu1_dials.set_dial_image` | Set background image from media library |
| `vu1_dials.reload_dial` | Reload dial hardware configuration |
| `vu1_dials.calibrate_dial` | Calibrate dial needle position |

### Service Examples

```yaml
# Set dial value
service: vu1_dials.set_dial_value
data:
  dial_uid: "590056000650564139323920"
  value: 50

# Set backlight to orange
service: vu1_dials.set_dial_backlight
data:
  dial_uid: "590056000650564139323920"
  red: 100
  green: 50
  blue: 0

# Set background image
service: vu1_dials.set_dial_image
data:
  dial_uid: "590056000650564139323920"
  media_content_id: "media-source://media_source/local/dial_backgrounds/cpu.png"
```

## Options

Configure via **Devices & Services** → **VU1 Dials** → **Configure**:

- **Update interval** - How often to poll the server (default: 30 seconds)
- **Timeout** - API request timeout (default: 10 seconds)

## Requirements

- Home Assistant 2023.3.0+
- VU1 Server running (add-on or standalone)
- Valid API key from VU1 Server

## Troubleshooting

### Dial not responding
1. Check VU1 Server is running and accessible
2. Verify API key is correct
3. Use the "Refresh hardware info" button
4. Check Home Assistant logs for errors

### Sensor binding not updating
1. Verify update mode is set to "Automatic"
2. Check the bound entity exists and has a numeric state
3. Ensure value min/max range is configured correctly

## Support

- [GitHub Issues](https://github.com/leoherzog/home-assistant-vu1-devices/issues)

## License

This project is licensed under the MIT License.
