# MicroAir EasyTouch RV — MQTT Protocol Reference

Reverse engineered from APK `com.microair.android.easyzone_rv` (resources.arsc + jadx decompile of classes4.dex).

---

## Connection

| Parameter | Value |
|---|---|
| Broker | `al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com` |
| Port | `8883` (MQTT over TLS) |
| Auth | Mutual TLS — AWS IoT certificate + key |
| CA | Amazon Root CA 1 |

The broker sits behind AWS IoT Core load balancing — the hostname resolves to multiple IPs. Always connect by hostname, never by IP.

---

## Authentication / Provisioning

Authentication uses AWS Cognito (SRP flow) → AWS IoT certificate.

| Parameter | Value |
|---|---|
| Region | `us-east-1` |
| Cognito User Pool ID | `us-east-1_M9Hs9kugG` |
| Cognito App Client ID | `2mfujqa7vidd2td9h08k9m061s` |
| Cognito Identity Pool ID | `us-east-1:527ec272-ac1b-4301-8e46-464669790d6e` |
| IoT Policy | `MyAndroidPolicy` |

Provisioning flow:
1. Authenticate with Cognito User Pool via `USER_SRP_AUTH`
2. Exchange ID token for temporary AWS credentials via Identity Pool
3. Call `iot:DescribeEndpoint` to confirm broker hostname
4. Call `iot:CreateKeysAndCertificate` to provision a client certificate
5. Call `iot:AttachPolicy` to attach `MyAndroidPolicy` to the certificate
6. Connect to broker using the provisioned certificate

Cognito refresh tokens are valid for ~30 days and can be used to obtain new ID tokens without re-entering credentials.

---

## Topics

| Direction | Topic format |
|---|---|
| Subscribe (device → app) | `EasyTouch <serial>` |
| Publish (app → device) | `EasyTouch <serial>` |

Both directions use the same base topic. Example for serial `352016109`:
```
EasyTouch 352016109
```

---

## Message Format

All messages are JSON. App-to-device commands use a `Type` field; device-to-app responses use `Type: "Response"`.

---

## Device → App: Status Response

```json
{
  "Type": "Response",
  "RT": "Status",
  "TT": "EasyTouch",
  "SN": "352016109",
  "REV": "1.0.7.0",
  "alertLL": 40,
  "alertUL": 90,
  "CI": 129,
  "hA": 0,
  "PRM": [0, 11, 63, 77],
  "Z_sts": {
    "0": [68, 72, 76, 68, 72, 45, 0, 128, 128, 128, 2, 128, 77, 255, 0, 3]
  }
}
```

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `RT` | string | Response type (`"Status"`, etc.) |
| `TT` | string | Thing type (`"EasyTouch"`) |
| `SN` | string | Device serial number |
| `REV` | string | Firmware revision |
| `alertLL` | int | Low temperature alert threshold (°F) |
| `alertUL` | int | High temperature alert threshold (°F) |
| `CI` | int | Capabilities bitmask |
| `hA` | int | Home/away mode: `0` = home, `1` = away. Presence of this field indicates the device supports home/away mode. |

### `PRM` array — `[displayedZone, flags, weatherTemp, faceplateTemp]`

| Index | Field | Description |
|---|---|---|
| 0 | `DisplayedZone` | Currently displayed zone number |
| 1 | flags | Bitmask (see below) |
| 2 | `weatherTemperature` | Outside weather temperature (°F), from internet feed |
| 3 | `faceplateTemperature` | Temperature shown on device faceplate (°F) |

**`PRM[1]` flag bits:**

| Bit | Mask | Field | Meaning when set |
|---|---|---|---|
| 0 | `0x01` | `wifiConnected` | Wi-Fi connected |
| 1 | `0x02` | `awsConnected` | AWS/cloud connected |
| 2 | `0x04` | `pushNotifyEnabled` | Push notifications enabled |
| 3 | `0x08` | `systemPower` | System power on |
| 4 | `0x10` | `scheduleEnabled` | Schedule active |
| 5 | `0x20` | `useAquaIcon` | Aqua/hydronic icon mode |
| 6 | `0x40` | `useAwaySettings` | Away settings active |

### `Z_sts` — Zone status array (16 bytes per zone)

The `Z_sts` object has one key per zone (e.g. `"0"`, `"1"`). Each zone is a 16-element integer array. `128` (0x80) and `255` (0xFF) are sentinel values meaning not available/not applicable.

| Index | Field | Description |
|---|---|---|
| 0 | `autoHeatSP` | Auto mode heat setpoint (°F) |
| 1 | `autoCoolSP` | Auto mode cool setpoint (°F) |
| 2 | `coolSP` | Cool setpoint (°F) |
| 3 | `heatSP` | Heat setpoint (°F) |
| 4 | `drySP` | Dry mode setpoint (°F) |
| 5 | `rhSP` | Humidity setpoint (%) |
| 6 | `fanOnlySpeed` | Fan-only mode fan speed |
| 7 | `coolFanSpeed` | Cooling mode fan speed |
| 8 | `electricFanSpeed` | Electric heat fan speed |
| 9 | `autoFanSpeed` | Auto mode fan speed |
| 10 | `mode` | Operating mode (see Mode enum) |
| 11 | `gasFanSpeed` | Gas heat fan speed |
| 12 | `ambient` | Current indoor temperature (°F) |
| 13 | `outsideAirTemp` | Outside air temperature (°F), 255 = not available |
| 14 | `fault` | Fault code (see Fault codes) |
| 15 | `statusFlags` | Bitmask (see below) |

**`Z_sts[15]` status flag bits:**

| Bit | Mask | Field | Meaning when set |
|---|---|---|---|
| 0 | `0x01` | `cycleActive` | Compressor/cycle is active |
| 1 | `0x02` | `isCooling` | Unit is actively cooling |
| 2 | `0x04` | `isHeating` | Unit is actively heating |
| 3 | `0x08` | `electricHeatOverrideActive` | Electric heat override active |
| 4 | `0x10` | `electricHeatLockoutActive` | Electric heat lockout active |
| 5 | `0x20` | `isAutoHeat` | Auto mode currently in heat cycle |

**Fan speed values:**

| Value | Meaning |
|---|---|
| 0 | Auto |
| 1 | Low |
| 2 | Medium |
| 3 | High |
| 128 | Not applicable |

**Fault codes (`Z_sts[14]`):**

| Value | Meaning |
|---|---|
| 0 | No fault |
| 1 | No communication |
| 2 | Bad remote sensor |
| 3 | Bad outside sensor |
| 4 | Bad freeze sensor |
| 5 | Freeze detected |
| 6 | Bad humidity sensor |
| 7 | No AC power |
| 8 | Invalid config |
| 9 | Low DC voltage |
| 10 | Bad indoor sensor |
| 11 | Inside coil sensor fault |
| 12 | Outside coil sensor fault |
| 13 | Low refrigerant |
| 14 | Undefined fault |

---

## Mode Enum (`Mode_Class.java`)

Used in `Z_sts[10]` and in change commands. The upper nibble may carry flags; use `mode & 0x0F` to extract the base mode.

| Value | Constant | Display name |
|---|---|---|
| 0 | `mode_off` | Off |
| 1 | `mode_fan_only` | Fan Only |
| 2 | `mode_cool` | Cool |
| 3 | `mode_heat` | Gas Heat |
| 4 | `mode_furnace` | Furnace |
| 5 | `mode_heat_pump` | Heat Pump |
| 6 | `mode_dry` | Dry |
| 7 | `mode_heat_strip` | Heat Strip |
| 8 | `mode_auto` | Auto |
| 9 | `mode_autoHS` | Auto + Heat Strip |
| 10 | `mode_autoHP` | Auto + Heat Pump |
| 11 | `mode_autoFurnace` | Auto + Furnace |
| 12 | `mode_electricHeat` | Electric Heat |
| 13 | `mode_gasHeat` | Gas Heat (alt) |
| 16 | `mode_zone_off` | Zone Off |

Auto modes (8–11) show two setpoints (heat + cool). Heat modes: 3, 4, 5, 7, 13.

---

## App → Device: Commands

All commands are published to `EasyTouch <serial>`.

### Get Status
```json
{"Type": "Get Status", "Zone": 0, "TM": 1749479400, "LAT": "38.97916", "LON": "-74.89763", "DST": 60}
```

| Field | Type | Description |
|---|---|---|
| `Zone` | int | Zone index (0-based) |
| `TM` | int | Unix timestamp (seconds UTC) — sent every poll for device clock sync |
| `LAT` | string | Latitude, 5 decimal places — used by device to fetch local weather |
| `LON` | string | Longitude, 5 decimal places |
| `DST` | int | Current DST offset in minutes (60 when DST active, 0 otherwise) |

`TM` is included on every poll. `LAT`/`LON`/`DST` are sent on first connect and approximately once per hour. The Android app only sends location over Bluetooth; the iOS app sends it over MQTT as well. Format confirmed from `U_Thermostat.java` (`sendStatusRequest()`).

### ExtraData
```json
{"Type": "ExtraData", "Zone": 0}
```

Sent by the iOS app to request supplemental device metadata. Response:
```json
{"Type": "Response", "RT": "ExtraData", "TT": "EasyTouch", "SN": "352016364", "REV": "1.0.7.0", "Zone": 0, "SS": 27, "OS": 0, "AO": 3}
```

| Field | Description |
|---|---|
| `TT` | Device type string |
| `SN` | Serial number |
| `REV` | Firmware version |
| `SS` / `OS` / `AO` | Unknown — observed values: 27, 0, 3 |

### Get Config
```json
{"Type": "Get Config", "Zone": 0}
```

### Get Modes
```json
{"Type": "Get Modes"}
```

### Get Schedule
```json
{"Type": "Get Schedule"}
{"Type": "Get Schedule", "Day": 0}
```

### Change Setpoints
```json
{
  "Type": "Change",
  "Changes": {
    "zone": 0,
    "cool_sp": 72,
    "heat_sp": 68,
    "dry_sp": 72,
    "autoHeat_sp": 68,
    "autoCool_sp": 72
  }
}
```

### Change Mode
```json
{"Type": "Change", "Changes": {"zone": 0, "mode": 2}}
```

### Power On/Off
```json
{"Type": "Change", "Changes": {"zone": 0, "mode": 0}}
```
Power off is achieved by setting mode to `0` (off). The last active mode is stored by the app and restored on power on.

### Change Fan Speed
```json
{"Type": "Change", "Changes": {"zone": 0, "fan": "fan_auto"}}
{"Type": "Change", "Changes": {"zone": 0, "fan": "fan_low"}}
{"Type": "Change", "Changes": {"zone": 0, "fan": "fan_med"}}
{"Type": "Change", "Changes": {"zone": 0, "fan": "fan_high"}}
```

### Temperature Alert Thresholds
```json
{"Type": "Change", "Changes": {"zone": 0, "alertLL": 40}}
{"Type": "Change", "Changes": {"zone": 0, "alertUL": 90}}
```

Fields are sent individually, not together. Constraints (enforced by Android app, `Notifications.java`):

| Field | Min | Max | Rule |
|---|---|---|---|
| `alertLL` | 40°F | 108°F | Must be ≤ `alertUL` − 2 |
| `alertUL` | 42°F | 110°F | Must be ≥ `alertLL` + 2 |

The current values are reported in every status response as `alertLL` / `alertUL` top-level fields.

### Calibration / Reset
```json
{"Type": "Change", "Changes": {"zone": 0, "cal": "OK"}}
{"Type": "Change", "Changes": {"zone": 0, "reset": "OK"}}
```

### Wi-Fi Scan
```json
{"Type": "Get SSIDs", "Action": "scan"}
```

### Firmware Update
```json
{"Type": "UPDATE", "Action": "..."}
```

---

## Firmware version note

The `REV` field in status responses contains the firmware revision string (e.g. `"1.0.7.0"`). The app checks whether firmware is newer than revision 5.x to decide which protocol version to use. Devices on firmware older than rev 5 use a different (OldRev4) protocol with a different `Z_sts` layout — not documented here.

---

## Device → App: Config Response (`RT: "Config"`)

Sent in response to `Get Config`. Contains per-zone configuration in a `CFG` string field, which is itself a JSON object with one key per zone (`"zone0"`, `"zone1"`, etc.).

Each zone config object:

| Field | Type | Description |
|---|---|---|
| `Zone` | int | Zone number |
| `MAV` | int | Available modes bitmask — bit N set means mode N is available |
| `SPL` | array | Setpoint limits: `[minCoolSP, maxCoolSP, minHeatSP, maxHeatSP]` (°F) |
| `MA` | array | Mode array (16 ints) — per-mode configuration |
| `FA` | array | Fan array (16 ints) — per-mode fan configuration |

The app supports up to 4 zones (0–3). If all zones have `MAV = 0` the config is considered invalid.

---

## App → Device: Schedule Commands

### Get schedule for a day
```json
{"Type": "Get Schedule", "Day": 0}
```
Day: 0 = Sunday, 1 = Monday, ... 6 = Saturday.

### Set schedule for a day
```json
{
  "Type": "Change",
  "day": 1,
  "data": [
    [time, heatSP, coolSP, modeAndZones, fan],
    [time, heatSP, coolSP, modeAndZones, fan]
  ]
}
```

Note: `"data"` is an **array of 5-element arrays** (one per slot), not a flat array. Uses `"day"` / `"data"` at the top level, not the `"Changes"` wrapper used by other commands. Source: `Schedule_Main6.java`.

#### Schedule slot fields

| Index | Field | Description |
|---|---|---|
| 0 | `time` | Bit-packed time (see below). `128` (`0x80`) = slot disabled |
| 1 | `heatSP` | Heat setpoint (°F) |
| 2 | `coolSP` | Cool setpoint (°F) |
| 3 | `modeAndZones` | Lower nibble = mode, upper nibble = selected zones bitmask |
| 4 | `fan` | Fan speed (see below) |

#### `time` encoding (bit-packed byte)

| Bits | Field | Notes |
|---|---|---|
| 7 | Disabled | `1` = slot inactive, `0` = active |
| 6–2 | Hour | 0–23, extracted as `(time >> 2) & 0x1F` |
| 1–0 | Quarter | 0=:00, 1=:15, 2=:30, 3=:45 |

Encoding: `time = (hour << 2) | quarter`. To disable a slot: `time |= 0x80`.

Examples: `8` = 2:00 AM, `11` = 2:45 AM, `48` = 12:00 PM, `128` = disabled.

#### `modeAndZones` encoding

`modeAndZones = mode | (selectedZones << 4)`

`selectedZones` is a bitmask where bit 0 = zone 1, bit 1 = zone 2, etc. To decode: `mode = value & 0x0F`, `zones = value >> 4`.

#### `fan` encoding

| Bit(s) | Meaning |
|---|---|
| 7 | Full auto (`128` = auto) |
| 6 | Semi-auto |
| 3–0 | Speed level (1–15); `0` = off |

Use `128` for auto fan. Fixed speeds use the lower nibble.

### Schedule response

```json
{"Type": "Response", "RT": "Schedule", "DAY": 1, "EVNT": {
  "0": [time, heatSP, coolSP, modeAndZones, fan],
  "1": [time, heatSP, coolSP, modeAndZones, fan]
}}
```

`EVNT` is a dict keyed by slot index string (`"0"`, `"1"`, …). Same field encoding as the set command.

---

## App → Device: Control Settings Commands

### Enable/disable schedule
```json
{"Type": "Change", "Changes": {"zone": 0, "clockFlags": 3, "Schedule": 1}}
```
`Schedule`: 1 = enable, 0 = disable. `clockFlags` bit 1 (mask `0x02`) = schedule enabled.

### Set home/away mode
```json
{"Type": "Change", "Changes": {"zone": 0, "hA": 1}}
```
`hA`: 0 = home, 1 = away. Only sent if device advertises home/away support (presence of `hA` field in status response).

### Send current time to device
The app sends `clockFlags` as part of schedule enable/disable to sync the device clock.

---

## Topic reference (from decompiled source)

The topic used for both publish and subscribe is stored in `EasyTouch_RV.mSelectedTopic`. Format confirmed from source:

```
EasyTouch <serial>
```

The same topic is used for all message types in both directions. There are no separate command/status subtopics.

---

## Transport fallback

The app supports two transports — it selects based on `EasyTouch_RV.mConnectWifi`:
- **Wi-Fi / AWS IoT MQTT** — primary, used when Wi-Fi connected
- **Bluetooth LE** — fallback, writes JSON bytes directly via `BluetoothLeService`

Both transports use the same JSON message format.
