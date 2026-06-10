# Micro-Air EasyTouch Wi-Fi — Home Assistant Integration

A custom Home Assistant integration for the [Micro-Air EasyTouch RV thermostat](https://micro-air.com/products/easytouch-rv-thermostat) using Wi-Fi/MQTT via the same AWS IoT cloud infrastructure as the official Android app.

This integration was built as a more reliable alternative to the existing BLE-based integration, which suffers from frequent Bluetooth connection drops in RV environments.

---

## Features

- **Climate control** — set HVAC mode, target temperature, fan speed, and heat source
- **Multi-zone support** — zones discovered dynamically; each gets its own climate entity
- **Heat source presets** — select from Heat Pump, Furnace, Heat Strip, Electric Heat, Gas Heat (based on what your unit supports)
- **Auto mode** — independent heating/cooling setpoints
- **Fan control** — Auto, Low, Medium, High (mode-dependent)
- **Optimistic updates** — UI responds immediately; no waiting for the device to echo back state
- **Firmware updates** — checks for available firmware daily via AWS and installs with one click; device downloads and reboots autonomously
- **Temperature alerts** — configure low and high temperature alert thresholds (40–110°F) that trigger push notifications from the Micro-Air app
- **Weather location** — sends your HA home coordinates to the thermostat on connect and hourly so the device displays accurate local weather
- **Diagnostic sensors** — serial number, firmware version, model, device type, MQTT endpoint
- **Cloud connectivity sensor** — know when the device loses its AWS connection
- **Reboot button** — send a reboot command over the cloud connection
- **Bluetooth Reboot button** — send a reboot command via Bluetooth LE as a fallback when the thermostat has lost its cloud connection
- **No local infrastructure required** — connects directly to AWS IoT Core using mutual TLS

---

## Requirements

- Home Assistant 2024.1.0 or newer
- A Micro-Air EasyTouch RV thermostat with Wi-Fi (firmware rev 5+)
- A Micro-Air app account (the same username/password used in the Android/iOS app)
- Your device's serial number (printed on the thermostat or visible in the app)

---

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category: Integration).
2. Install **Micro-Air EasyTouch Wi-Fi**.
3. Restart Home Assistant.

### Manual

1. Copy the `custom_components/ha_easytouch_wifi` directory into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.

---

## Setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Micro-Air EasyTouch Wi-Fi**.
3. Enter your Micro-Air app **username** and **password**.
   - Home Assistant will authenticate with AWS Cognito and provision a unique IoT certificate for this installation. This takes a few seconds.
4. Enter your thermostat's **serial number** (e.g. `352016109`).
5. The device will appear with all entities automatically.

To add a second thermostat under the same account, repeat the process with the other serial number — each thermostat gets its own config entry and IoT certificate.

---

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| EasyTouch *serial* | `climate` | Main climate entity (single-zone) or Zone N (multi-zone) |
| Cloud Connected | `binary_sensor` | Whether the thermostat has an active AWS IoT connection |
| Data Healthy | `binary_sensor` | Turns `on` (problem) if no data received within 60 seconds |
| Serial Number | `sensor` | Device serial number (diagnostic) |
| Firmware Version | `sensor` | Firmware revision reported by the device (diagnostic) |
| Model Number | `sensor` | Model string from device config (diagnostic) |
| Device Type | `sensor` | Device type from config (diagnostic) |
| MQTT Endpoint | `sensor` | AWS IoT hostname in use (diagnostic) |
| Firmware | `update` | Shows installed vs latest firmware; install button sends update command to device |
| Low Temperature Alert | `number` | Alert threshold lower bound (40–108°F); triggers app push notification |
| High Temperature Alert | `number` | Alert threshold upper bound (42–110°F); triggers app push notification |
| Reboot | `button` | Send a reboot command over the cloud (MQTT) connection |
| Bluetooth Reboot | `button` | Send a reboot command via Bluetooth LE — works when the cloud connection is down |
| Thermostat Wi-Fi | `binary_sensor` | Device-reported Wi-Fi connection status (unavailable when data is stale) |
| Thermostat Cloud | `binary_sensor` | Device-reported AWS IoT connection status (unavailable when data is stale) |

---

## How It Works

The thermostat connects to AWS IoT Core over MQTT (port 8883, mutual TLS). During setup, this integration authenticates with Micro-Air's Cognito user pool using the SRP auth flow and provisions a new IoT certificate under your account — the same mechanism used by the Android app.

The coordinator connects directly to the same AWS IoT endpoint, subscribing to the device's MQTT topic (`EasyTouch <serial>`) and any subtopics. Status is polled every 10 seconds via a `Get Status` command; zone configuration is requested on connect. Location coordinates (from HA's home location) and a Unix timestamp are included in the first poll and approximately once per hour so the thermostat can display accurate local weather.

The provisioned certificate is stored encrypted in your Home Assistant config entry. AWS IoT certificates do not expire and remain valid until explicitly revoked. Server certificate verification uses the system CA bundle, which includes all Amazon Trust Services root CAs.

---

## Technical Notes

- Uses `paho-mqtt` 2.x directly (not HA's built-in MQTT component) — required for mutual TLS with AWS IoT certificates
- SSL context uses `ssl.create_default_context()` with the system CA bundle; the provisioned client cert/key are loaded via temporary files
- The MQTT network loop runs in a background thread; callbacks bridge to the HA event loop via `call_soon_threadsafe`
- 500ms debounce on temperature and fan changes prevents command flooding during slider adjustments
- 5-second status suppression after sending a command prevents the UI from bouncing back before the device applies the change
- paho's built-in reconnect handles transient drops; zone subscriptions are re-established on reconnect
- Location format (5 decimal places, DST in minutes) validated against Android app source (`U_Thermostat.java`)

---

## Troubleshooting

**Authentication fails during setup**
- Double-check your Micro-Air app username and password.
- Ensure you have an active internet connection — provisioning requires AWS API access.

**Device shows unavailable**
- Check the **Cloud Connected** sensor. If it's `off`, HA itself has lost its MQTT connection to AWS.
- Check the **Data Healthy** sensor. If it's `on` (problem), no data has been received in 60 seconds — the thermostat may have lost its internet connection even though HA's connection is fine.
- Check the **Thermostat Wi-Fi** and **Thermostat Cloud** sensors (when data is fresh) to see the device's own reported connectivity status.
- Check Home Assistant logs for MQTT connection errors.

**Thermostat lost internet connection**
- If **Cloud Connected** is `on` but **Data Healthy** is `on` (problem), the thermostat can't reach AWS but HA can.
- Try the **Bluetooth Reboot** button to restart the thermostat via BLE — useful when the device has gotten into a bad connectivity state.
- The HA host must have a Bluetooth adapter and be within BLE range (~10m) of the thermostat.
- You can automate this: trigger **Bluetooth Reboot** when **Data Healthy** has been `on` for several minutes while **Cloud Connected** is also `on`.

**Bluetooth Reboot button doesn't work**
- Ensure the HA host has a Bluetooth adapter.
- The thermostat must be within Bluetooth range — check HA logs for "BLE scan failed" or "not found" messages.
- The button will run an active 5-second BLE scan if the device isn't in HA's passive scan cache; this is normal and expected.

**Wrong temperature unit**
- The integration uses Fahrenheit, matching the device's native protocol. Use Home Assistant's unit conversion if needed.

---

## Related Projects

- [ha-easytouch](https://github.com/phurth/ha-easytouch) — the original BLE-based integration this project was built to complement
- [PROTOCOL.md](docs/PROTOCOL.md) — full reverse-engineered protocol reference (MQTT topic format, Z_sts field map, mode enum, command formats)
