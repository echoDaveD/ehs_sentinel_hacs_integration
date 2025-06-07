# EHS Sentinel Home Assistant Integration

This custom integration starts a background Python script for serial or MQTT-based data acquisition.

## Features
- Configuration via Home Assistant UI
- Launches async Python logic from `startEHSSentinel.py`
- Ideal for device bridges or protocol interfaces

## Installation

1. Copy this repository to `config/custom_components/ehs_sentinel`
2. Restart Home Assistant
3. Go to Settings > Devices & Services > Add Integration > Search "EHS Sentinel"

## Configuration Options
- `serial_port`: Serial device path
- `mqtt_host`: MQTT broker hostname
- `dry_run`: Run without actual communication
