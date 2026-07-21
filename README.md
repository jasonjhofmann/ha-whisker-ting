# Whisker Ting Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/billda/ha-ting-fire.svg)](https://github.com/billda/ha-ting-fire/releases)

Home Assistant integration for the [Whisker Labs Ting](https://www.tingfire.com/) electrical-fire-safety sensor. Ting plugs into an outlet and continuously monitors your home's electrical system for the arcing and power-quality problems that precede electrical fires. This integration connects to the Ting cloud service on your behalf and exposes real-time voltage readings plus electrical, utility, and power-quality hazard status as native Home Assistant entities.

## Features

- **Real-time voltage monitoring** via a WebSocket connection to the Ting cloud
  - Current voltage, voltage high/low, and average peak voltage
- **Fire hazard status** monitoring
  - Electrical Fire Hazard (EFH) detection
  - Utility Fire Hazard (UFH) detection
  - Learning-mode status
- **Power quality hazard** and **frozen pipe risk** detection (site-level)
- **Connectivity sensor** — reflects whether the real-time data stream is live
- **Device diagnostics** — firmware version, WiFi/Bluetooth MAC addresses (also registered as device connections), serial number, subscription start date, and account group

## Installation

### HACS (recommended)

Whisker Ting is not (yet) in the default HACS store, so add it as a custom repository:

1. Open **HACS** in Home Assistant.
2. Click the three-dot menu in the top right corner and select **Custom repositories**.
3. Add `https://github.com/billda/ha-ting-fire` as the repository URL, choose **Integration** as the category, and click **Add**.
4. Find **Whisker Ting** in HACS and click **Download**.
5. Restart Home Assistant.

### Manual

1. Download the latest release from [GitHub](https://github.com/billda/ha-ting-fire/releases), or clone the repository.
2. Copy the `custom_components/whisker_ting` folder into your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

## Configuration

1. Go to **Settings** → **Devices & Services** and click **+ Add Integration**.
2. Search for **Whisker Ting**.
3. Enter your Ting account credentials:

| Field | Description |
|---|---|
| **Email** | The email address for your Whisker Labs / Ting account. |
| **Password** | The password for that account — the same credentials you use in the Ting mobile app. |

These credentials are verified against the Ting cloud during setup; setup will not complete with incorrect credentials.

## Options

After setup, open **Settings** → **Devices & Services** → **Whisker Ting** and click **Configure** to change:

| Option | Description |
|---|---|
| **Update interval** | How often, in seconds, the integration polls the Ting API for hazard and diagnostic data. Range 30–3600, default 60. Real-time voltage sensors are unaffected by this setting — they update continuously from the WebSocket stream. |

## Removal

1. Go to **Settings** → **Devices & Services**.
2. Find the **Whisker Ting** integration card, open its three-dot menu, and select **Delete**.
3. If it was installed through HACS, open **HACS** → **Integrations**, find **Whisker Ting**, open its three-dot menu, and select **Remove** to remove the repository and files.

## Entities

### Sensors

| Name | Description | Category | Enabled by default |
|---|---|---|---|
| Current voltage | Real-time line voltage from the WebSocket stream | — | Yes |
| Voltage high | Real-time peak high voltage from the WebSocket stream | — | Yes |
| Voltage low | Real-time peak low voltage from the WebSocket stream | — | Yes |
| Average peaks max | Rolling average of peak voltage from the WebSocket stream | — | No |
| Hazard Status | Overall hazard status: `no_hazards`, `hazard_detected`, `reviewed_not_fire`, or `learning` | — | Yes |
| Hazard Message | Human-readable hazard summary from Ting | — | Yes |
| Electrical Fire Hazard Status | Raw EFH status code from Ting | — | Yes |
| Electrical Fire Hazard Message | Human-readable EFH message | — | Yes |
| Electrical Fire Hazard Level | EFH severity level | — | Yes |
| Utility Fire Hazard Status | Raw UFH status code from Ting | — | Yes |
| Utility Fire Hazard Message | Human-readable UFH message | — | Yes |
| Device Type | Ting device type reported by the API | Diagnostic | Yes |
| Firmware Version | Installed firmware version | Diagnostic | No |
| WiFi MAC Address | Device's WiFi MAC address (also registered as a device connection) | Diagnostic | No |
| Bluetooth MAC Address | Device's Bluetooth MAC address (also registered as a device connection) | Diagnostic | No |
| Serial Number | Device serial number | Diagnostic | No |
| Subscription Start | Timestamp the Ting subscription began | Diagnostic | No |
| Group | Ting account group/location name | Diagnostic | No |

### Binary sensors

| Name | Description | Category | Enabled by default |
|---|---|---|---|
| Fire Hazard | On when Ting reports any active fire hazard | — | Yes |
| Electrical Fire Hazard | On when an electrical fire hazard (EFH) is active | — | Yes |
| Utility Fire Hazard | On when a utility-side fire hazard (UFH) is active | — | Yes |
| Frozen Pipe Risk | On when Ting detects a frozen-pipe risk condition | — | Yes |
| Power Quality Hazard | On when a site-level power-quality hazard is detected | — | Yes |
| Connectivity | On while the real-time WebSocket stream is live | Diagnostic | Yes |
| Learning Mode | On while Ting is in its initial learning period | — | Yes |
| HVAC Verified | On when Ting has verified HVAC equipment on the circuit | Diagnostic | No |
| Is Owner | On when this account is the device owner (vs. a shared/guest user) | Diagnostic | No |

> **Note:** the Utility Fire Hazard binary sensor's entity ID keeps the legacy `unverified_fire_hazard` key for backward compatibility with existing installs — only its display name changed.

## Requirements

- Home Assistant 2024.12.0 or newer
- A Whisker Labs Ting device
- A Whisker Labs account

## Troubleshooting

### Voltage shows "Unknown" briefly on startup

This is normal — the integration waits for the WebSocket connection to receive its first data packet before displaying values.

### Voltage sensors show "Unavailable"

The real-time voltage sensors depend on a live WebSocket stream. If the stream disconnects and cannot be re-established, these sensors report unavailable (rather than a frozen last value) until the stream recovers. The hazard and diagnostic sensors continue to update from the regular API poll regardless of stream state.

### Authentication errors

Ensure you're using the same email and password you use in the Whisker Labs / Ting mobile app. If your password has changed, Home Assistant will prompt you to reauthenticate the integration; repeated errors after reauthenticating usually mean the credentials themselves are incorrect.

## Credits

This integration is not affiliated with or endorsed by Whisker Labs, Inc.

- Original integration by **Aiden Mitchell**.
- Fixes adopted from forks by **simplytoast1** and **adamjthompson**, including the SignalR stream authorization header that restores real-time voltage data.

## License

MIT License — see [LICENSE](LICENSE) for details.
