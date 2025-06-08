# Home Assistant Streacom VU1 Dials Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

A Home Assistant custom integration for Streacom VU1 eInk dials. This integration communicates with a VU1 server (either running locally or on the network) to provide Home Assistant control over your VU1 dials.

## Features

- **Auto-discovery** of VU1 server on localhost:5340
- **Device registration** for each VU1 dial as separate devices in Home Assistant
- **Sensor entities** showing current dial values and status
- **Services** for controlling dials:
  - Set dial value (0-100%)
  - Set RGB backlight color
  - Set dial name
  - Reload dial configuration
  - Calibrate dial
- **Real-time updates** with configurable polling interval
- **Device information** including backlight colors, images, and detailed status

## Requirements

- Home Assistant Core 2023.3.0 or newer
- A running VU1 server (see [VU1 Documentation](./VU-Documentation/) for setup)
- Valid API key for the VU1 server

## Installation

### HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed
2. Add this repository as a custom repository in HACS:
   - Go to HACS > Integrations > Three dots menu > Custom repositories
   - Repository: `leoherzog/home-assistant-vu1-devices`
   - Category: `Integration`
3. Click "Install" on the "Streacom VU1 Dials" integration
4. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/vu1_dials` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

### Setup Flow

1. Go to **Settings** > **Devices & Services**
2. Click **"+ Add Integration"**
3. Search for **"Streacom VU1 Dials"**
4. The integration will attempt to auto-discover a VU1 server on `localhost:5340`
5. If discovered, confirm the host/port and enter your API key
6. If not discovered, manually enter:
   - Host (default: localhost)
   - Port (default: 5340)
   - API Key

### Configuration Options

After setup, you can configure:
- **Update Interval**: How often to poll the VU1 server (5-300 seconds, default: 30)

## Usage

### Entities

Each VU1 dial becomes a sensor entity in Home Assistant:
- **Entity ID**: `sensor.vu1_dial_<dial_uid>`
- **State**: Current dial value (0-100%)
- **Attributes**:
  - `dial_uid`: Unique identifier
  - `dial_name`: Display name
  - `backlight_red/green/blue`: Current RGB values
  - `image_file`: Background image file
  - `detailed_status`: Additional status information

### Services

#### `vu1_dials.set_dial_value`
Set the dial needle position (0-100%).

```yaml
service: vu1_dials.set_dial_value
data:
  dial_uid: "590056000650564139323920"
  value: 75
```

#### `vu1_dials.set_dial_backlight`
Set the RGB backlight color (0-100% for each channel).

```yaml
service: vu1_dials.set_dial_backlight
data:
  dial_uid: "590056000650564139323920"
  red: 50
  green: 25
  blue: 75
```

#### `vu1_devices.set_dial_name`
Change the display name of a dial.

```yaml
service: vu1_dials.set_dial_name
data:
  dial_uid: "590056000650564139323920"
  name: "CPU Usage"
```

#### `vu1_devices.reload_dial`
Reload dial configuration from the server.

```yaml
service: vu1_dials.reload_dial
data:
  dial_uid: "590056000650564139323920"
```

#### `vu1_devices.calibrate_dial`
Calibrate the dial hardware.

```yaml
service: vu1_dials.calibrate_dial
data:
  dial_uid: "590056000650564139323920"
```

### Automation Examples

#### CPU Usage Display
```yaml
automation:
  - alias: "Update CPU Dial"
    trigger:
      - platform: state
        entity_id: sensor.processor_use
    action:
      - service: vu1_dials.set_dial_value
        data:
          dial_uid: "590056000650564139323920"
          value: "{{ states('sensor.processor_use') | int }}"
```

#### Temperature with Color Coding
```yaml
automation:
  - alias: "Temperature Dial with Color"
    trigger:
      - platform: state
        entity_id: sensor.cpu_temperature
    action:
      - service: vu1_dials.set_dial_value
        data:
          dial_uid: "590056000650564139323920"
          value: "{{ ((states('sensor.cpu_temperature') | float - 30) / 70 * 100) | round }}"
      - service: vu1_dials.set_dial_backlight
        data:
          dial_uid: "590056000650564139323920"
          red: "{{ 100 if states('sensor.cpu_temperature') | float > 70 else 0 }}"
          green: "{{ 100 if states('sensor.cpu_temperature') | float < 60 else 0 }}"
          blue: 0
```

## Troubleshooting

### Connection Issues
- Ensure the VU1 server is running and accessible
- Verify the API key is correct
- Check firewall settings if using a remote server

### Dial Not Responding
- Try the `reload_dial` service
- Check the VU1 server logs
- Verify the dial UID is correct

### Performance
- Adjust the update interval in integration options
- Consider using automations triggered by state changes rather than polling

## VU1 Server Setup

This integration requires a VU1 server to be running. You can:

1. **Use the included Home Assistant Add-on** (in `home-assistant-vu1-server/`)
2. **Run the server elsewhere** and connect over the network

See the [VU1 Documentation](./VU-Documentation/) for detailed setup instructions.

## API Reference

For detailed API documentation, see [VU1 API Documentation](./VU-Documentation/docs/api/).

## Support

For issues and feature requests, please use the [GitHub Issues](https://github.com/leoherzog/home-assistant-vu1-devices/issues) page.

## License

This project is licensed under the MIT License.