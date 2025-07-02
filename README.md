# EHS Sentinel Home Assistant Integration

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=ehs_sentinel_hacs_integration&owner=echoDaveD)

This custom integration connects your Samsung EHS Sentinel system to Home Assistant, enabling real-time monitoring and control.

## Features

- Seamless integration with Home Assistant
- Auto-discovery of sensors, switches, numbers, selects, and binary sensors
- Asynchronous communication for fast updates
- Configuration via Home Assistant UI

## Installation

1. **Add this repository to HACS:**
   - Go to HACS > Integrations > ⋮ (menu) > Custom repositories
   - Add the repository URL: `https://github.com/echoDaveD/ehs_sentinel_hacs_integration`
   - Select category: Integration

2. **Install the integration:**
   - Search for "EHS Sentinel" in HACS > Integrations
   - Click "Install"

3. **Restart Home Assistant**

4. **Configure the integration:**
   - Go to Settings > Devices & Services > Add Integration
   - Search for "EHS Sentinel" and follow the setup instructions

## Configuration Options

- `ip`: IP of RS485 to ETH Adapter
- `port`: PORT of RS485 to ETH Adapter
- `polling`: Switch if Sentinel should poll some measurements
- `write-mode`: Switch if Entities of Sentinel are writable or only read only

## Support

For issues or feature requests, please open an issue on [GitHub](https://github.com/echoDaveD/ehs_sentinel_hacs_integration/issues).

---