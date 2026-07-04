# Micro-Air EasyTouch 352 — MQTT Protocol Notes

Firmware version: `1.0.7.0` (EasyTouch_352_1.0.7.0.elf)

---

## Connection

**Broker:** `al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com`  
**Port:** `443`  
**Transport:** MQTT over TLS (mutual authentication)

### Credentials (stored on SPIFFS)

| File | Contents |
|------|----------|
| `/spiffs/fonts/pem.crt` | Device certificate |
| `/spiffs/fonts/pem.key` | Device private key |
| `/spiffs/fonts/root.pem` | Root CA certificate |
| `/spiffs/fonts/serial.txt` | Device serial number (used as AWS IoT thing name / MQTT client ID) |

> The serial number from `serial.txt` is also used to identify the device in the cloud. The MQTT topics may be prefixed with this value at runtime (e.g., `{serial}/UPDATE`) — no static format string was found, so this is likely constructed in code.

---

## Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `UPDATE` | Cloud → Device (subscribed by device) | Device receives commands here |
| `PushNotify` | Device → Cloud (published by device) | Device sends status and notifications here |
| `Response` | Device → Cloud | Response/ACK channel (may be same as PushNotify) |

---

## Getting Device Config

Publish JSON to `UPDATE`:

```json
{"Sent_type": "Get Config"}
```

The device responds on `PushNotify`.

### All Supported `Sent_type` Values

| `Sent_type` | Description |
|-------------|-------------|
| `Get Status` | Current thermostat state |
| `Get Config` | Full device configuration |
| `Get Schedule` | Schedule data |
| `Get SSIDs` | Available WiFi networks |
| `Change` | Set a single value |
| `Changes` | Set multiple values |
| `Day` | Schedule day data |

---

## Config Response Fields

### Per-Zone Fields

Zones are keyed as `zone0`, `zone1`, etc. in the JSON payload.

| Field | Description |
|-------|-------------|
| `heat_sp` | Heat setpoint |
| `cool_sp` | Cool setpoint |
| `dry_sp` | Dry mode setpoint |
| `autoHeat_sp` | Auto mode heat setpoint |
| `autoCool_sp` | Auto mode cool setpoint |
| `coolFan` | Cool fan mode |
| `eleFan` | Electric heat fan mode |
| `gasFan` | Gas/furnace fan mode |
| `fanOnly` | Fan-only mode |
| `autoFan` | Auto fan mode |
| `power` | Zone power on/off |
| `Z_MODE` | Operating mode |
| `Z_sts` | Zone status |
| `Z_VALID` | Zone validity flag |

### NVS Zone Parameter Keys (internal)

These are the NVS storage keys corresponding to zone settings:

`Z_AHSP`, `Z_ACSP`, `Z_CSP`, `Z_HSP`, `Z_DSP`, `Z_RHSP`, `Z_FOS`, `Z_CFS`, `Z_EHFS`, `Z_MHFS`, `Z_AFS`, `Z_MODE`, `Z_VALID`

### Device-Level Fields

| Field | Description |
|-------|-------------|
| `Serial` | Device serial number |
| `Device Type` | Hardware model |
| `Revision` | Hardware revision |
| `ambient` | Ambient temperature reading |
| `humidity` | Humidity reading |
| `latitude` / `longitude` | GPS coordinates |
| `AlertMin` / `AlertMax` | Alert thresholds |
| `FW1`–`FW6` | Firmware file entries |
| `FS1`–`FS6` | Firmware file size entries |
| `ExtraData` | Extra device data |
| `CFG` | Config block |
| `MAV` | (unknown) |
| `SPL` | (unknown) |
| `PRM` | Parameters block |

### Operating Mode Values

Observed mode strings: `Cool`, `Cool/Heat Strip`, `Cool/Heat Pump`, `Cool/Furnace`, `Electric Heat`

State values: `Cooling`, `Heating`, `Operating`, `Standby`, `fault`

---

## Other NVS Keys (System Settings)

Stored under NVS namespaces `storage`, `Sys_Parms`, `Grp2Parm`, `options`:

| Key | Description |
|-----|-------------|
| `s_away` | Away mode flag |
| `s_coolhyst` | Cooling hysteresis |
| `s_heathyst` | Heating hysteresis |
| `s_bright` | Screen brightness |
| `s_parm` / `s_parmA` | System parameters |
| `s_Offset` | Temperature offset/calibration |
| `s_SSB` | (unknown) |
| `s_spl_fn` | (unknown) |
| `s_index` | (unknown) |
| `email_address` / `defaultEmail` | Alert email address |
| `wifi_pword` | WiFi password |
| `validZones` | Bitmask of valid zones |
| `silentBoot` | Silent boot flag |
| `day0A`–`day6A` | Schedule data per weekday |

---

## OTA Update URL

```
https://s3.us-east-1.amazonaws.com/microaireasytouchrvupdates/EasyTouch_RV_Images/
```

---

## Notes

- The AWS IoT SDK task is named `aws_iot_task`; a watchdog timer named `aws_timer` resets the device after 90 minutes if it cannot reconnect to AWS.
- Bluetooth provisioning is also supported (`BluetoothInit`, `bt_password`).
- A default Bluetooth password `Testing123!` was found in the binary.
- The device also runs a web server (fields `Web Server`, `Web URL`, `Port`).
