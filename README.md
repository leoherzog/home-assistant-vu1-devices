# Streacom VU1 Dials

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

A Home Assistant integration for Streacom VU1 eInk dials. Control dial values, RGB backlighting, and automatic sensor binding through Home Assistant.

## Features

- **Auto-discovery** of VU1 Server add-on or localhost server
- **Multiple entities** per dial: sensor, number, light, image, select, button
- **Automatic sensor binding** with value mapping
- **Behavior presets**: Responsive, Balanced, Smooth, Custom
- **Bidirectional name sync** between HA and VU1 server
- **RGB backlight control** with native color picker

## Installation

### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Install "Streacom VU1 Dials" integration
3. Restart Home Assistant

### Manual

1. Copy `custom_components/vu1_dials` to your HA `custom_components` directory
2. Restart Home Assistant

## Setup

1. **Settings** → **Devices & Services** → **Add Integration**
2. Search for "Streacom VU1 Dials"
3. Integration will auto-discover VU1 server or enter manually:
   - Host: localhost (or server IP)
   - Port: 5340
   - API Key: (from VU1 server)

## Configuration

Configure automatic sensor binding per dial:

1. **Devices & Services** → **VU1 Dials** → **Configure**
2. Select dial and update mode:
   - **Automatic**: Bind to HA sensor with value mapping
   - **Manual**: Control via number entity or services

## Usage

### Automatic Binding (Recommended)
Configure once via UI, integration handles real-time updates automatically.

### Manual Control
```yaml
# Set dial value
service: number.set_value
target:
  entity_id: number.vu1_dial_123456_value
data:
  value: 75

# RGB backlight
service: light.turn_on
target:
  entity_id: light.123456_backlight
data:
  rgb_color: [255, 128, 0]
```

## Requirements

- Home Assistant 2023.3.0+
- VU1 Server running (add-on or external)
- Valid API key

## Documentation

For detailed setup and VU1 server configuration, see [VU1 Documentation](./VU-Documentation/).

## Support

- [Issues](https://github.com/leoherzog/home-assistant-vu1-devices/issues)
- [Documentation](./VU-Documentation/)