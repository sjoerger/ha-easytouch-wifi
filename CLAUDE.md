# EasyTouch Wi-Fi — Project History

This file records the research, reverse engineering, and implementation work done to
integrate the MicroAir EasyTouch RV thermostat with Home Assistant via MQTT.

---

## Background

The thermostat has an existing Home Assistant BLE integration (`ha-easytouch`, in
`/Users/steve/temp/ha-easytouch`), but Bluetooth LE connections were unreliable. The
goal was to build a Wi-Fi/MQTT-based integration that uses the same AWS IoT cloud
infrastructure as the official Android app.

---

## Phase 1 — APK Reverse Engineering

**Source APK:** `com.microair.android.easyzone_rv`
**Extracted to:** `/Users/steve/temp/easy/`

### What was extracted

- **`AndroidManifest.xml`** — decoded binary manifest via Python UTF-16LE extraction
- **`res/ju.json`** — found an old/stale AWS config with a deprecated Cognito client ID
- **`resources.arsc`** — compiled resources binary containing current AWS credentials

The `strings` command and naive string extraction corrupted the Cognito client secret
by including ARSC UTF-8 length-prefix bytes (`33` prefix → trimmed to correct value).
A custom Python ARSC string pool parser was written to extract clean strings.

### Credentials found (resources.arsc)

| Key | Value |
|---|---|
| Cognito User Pool ID | `us-east-1_M9Hs9kugG` |
| Cognito App Client ID | `2mfujqa7vidd2td9h08k9m061s` |
| Cognito App Client Secret | `bbq2v6edtn25j0pgdus7tsi4fq93rgiqfq0g0ener8m896o2euu` |
| Identity Pool ID | `us-east-1:527ec272-ac1b-4301-8e46-464669790d6e` |
| IoT Endpoint | `al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com` |
| IoT Policy | `MyAndroidPolicy` |

The stale credentials in `res/ju.json` (`us-east-1_Ah228JNHR` / `15h5f3h4pm3nhpk69ovt3dugcq`)
caused `ResourceNotFoundException` during early testing — confirmed by error output.

---

## Phase 2 — Authentication & Provisioning

**Script:** `easyzone_iot_provision.py`

### Auth issues encountered and resolved

| Error | Cause | Fix |
|---|---|---|
| `ResourceNotFoundException` | Stale client ID from `res/ju.json` | Parsed `resources.arsc` properly |
| `Unable to verify secret hash` | Included wrong (prefixed) secret | Stripped `33` ARSC length prefix |
| `SECRET_HASH was not received` | Cleared the secret entirely | Restored correct secret |
| `USER_PASSWORD_AUTH flow not enabled` | Cognito client requires SRP auth | Switched from boto3 `initiate_auth` to `pycognito` (implements `USER_SRP_AUTH`) |

### Provisioning flow (confirmed working)

1. Cognito SRP auth (`USER_SRP_AUTH`) via `pycognito`
2. Exchange ID token for temporary AWS credentials via Identity Pool
3. `iot:DescribeEndpoint` → confirm broker hostname
4. `iot:CreateKeysAndCertificate` → generate client cert + key
5. `iot:AttachPolicy` → attach `MyAndroidPolicy` to certificate
6. Save `certs/ca.pem`, `certs/client.crt`, `certs/client.key`, `certs/endpoint.txt`

Provisioned certificate ID: `7b3b506109fd31f0e32998f8c7f007999dbd193fd1091bb7b7266979fb2c4169`

AWS IoT certificates do **not** expire (active until explicitly revoked). Cognito refresh
tokens last ~30 days; the script caches them to `cognito_refresh.token`.

---

## Phase 3 — Protocol Reverse Engineering

**Decompiler:** `jadx` (two passes — first without `--show-bad-code`, second with it)
**Key source files:**
- `com/microair/android/easyzone_rv/Controls/U_Thermostat.java` — `StoreResponse()` method defines exact Z_sts parsing
- `com/microair/android/easyzone_rv/Mode_Class.java` — authoritative mode integer enum
- `com/microair/android/easyzone_rv/Control_Settings/ScheduleRev6/Schedule_Main6.java` — schedule format
- `com/microair/android/easyzone_rv/Control_Settings/Control_Settings.java` — home/away and schedule enable

### Key protocol facts confirmed by decompilation

- **MQTT topic:** `EasyTouch <serial>` — same topic for both directions, no subtopics
- **Z_sts** is a 16-element integer array per zone; `128` and `255` are sentinel values
- **Mode value** uses lower nibble only: `mode & 0x0F`
- **Fan speeds in Z_sts:** `0`=Auto, `1`=Low, `2`=Med, `3`=High, `128`=N/A
- **Fan commands:** per-mode fields (`coolFan`, `eleFan`, `gasFan`, `autoFan`, `fanOnly`) with integer values
- **Schedule encoding:** `modeAndZones = mode | (selectedZones << 4)`
- **Firmware rev 5+** uses the current protocol; older devices use a different Z_sts layout
- **Transport fallback:** Wi-Fi MQTT primary, BLE fallback — both use identical JSON

### Live device data captured (serial 352016109)

```
PRM:  [0, 11, 63, 77]
  → PRM[1]=11 (0x0B): wifiConnected=1, awsConnected=1, systemPower=1
  → PRM[2]=63: outside weather temp
  → PRM[3]=77: faceplate temp

Z_sts['0']: [68, 72, 76, 68, 72, 45, 0, 128, 128, 128, 2, 128, 77, 255, 0, 3]
  → mode=2 (Cool), ambient=77°F, coolSP=76°F
  → statusFlags=3: cycleActive=1, isCooling=1
  → coolFanSpeed=128 (Auto)
```

---

## Phase 4 — Protocol Documentation

**File:** `PROTOCOL.md`

Complete reference documenting:
- AWS IoT connection parameters and mutual TLS auth
- Cognito provisioning flow (6 steps)
- MQTT topic format
- Full `Z_sts` 16-byte array field map
- `PRM` array with flag bitmasks
- Complete mode enum (0–16 from `Mode_Class.java`)
- Fan speed values and field names
- Fault codes (0–14)
- All command message formats (`Get Status`, `Get Config`, `Change`, `Get Schedule`, etc.)
- Config response structure (`CFG`, `MAV`, `SPL`, `FA` arrays)
- Schedule command format with `modeAndZones` encoding
- Control settings (home/away, schedule enable/disable)
- Firmware version note (rev 5+ required for current protocol)
- Transport fallback note

---

## Phase 5 — Device Watcher Script

**Script:** `watch_device.py`

- Reads certs from `./certs/` directory
- Accepts `--serial` argument and optional `--request-status` flag
- Subscribes to `EasyTouch <serial>` and `EasyTouch <serial>/#`
- Pretty-prints incoming JSON with labeled field descriptions
- Uses `paho.mqtt.client` with `CallbackAPIVersion.VERSION2` and mTLS

---

## Phase 6 — Home Assistant Integration

**Location:** `custom_components/ha_easytouch_wifi/`
**Domain:** `ha_easytouch_wifi`
**Reference BLE integration:** `/Users/steve/temp/ha-easytouch/custom_components/ha_easytouch/`

### Architecture

One config entry per thermostat, identified by serial number. Multiple thermostats
under the same Micro-Air account are added as separate entries (each gets its own
AWS IoT certificate).

### Config flow (3 steps)

1. **`user`** — Micro-Air app username + password
2. **`provision`** — background task with progress spinner; runs full AWS provisioning;
   cert/key/endpoint stored in config entry data (encrypted by HA at rest)
3. **`serial`** — device serial number; becomes config entry unique ID

### Coordinator (`coordinator.py`)

- `EasyTouchMQTTCoordinator` extends `DataUpdateCoordinator` (push-based)
- Uses `paho-mqtt` 2.x directly (not HA's built-in MQTT component) — required for
  mTLS with AWS IoT certificates
- paho network loop runs in a background thread; callbacks bridge to HA event loop
  via `call_soon_threadsafe`
- On connect: subscribes to `EasyTouch <serial>`, sends Get Config for zones 0–3
- Poll loop: `Get Status` every 10 seconds while connected
- paho built-in reconnect handles transient drops; `on_connect` re-subscribes on reconnect
- Post-command status suppression (5s) prevents UI bounce
- 500ms debounce on temperature and fan changes

### Entities per thermostat

| Platform | Entity | Notes |
|---|---|---|
| `climate` | Zone 1 (+ Zone N for multizone) | Dynamic — created when zones appear in Z_sts |
| `binary_sensor` | Cloud Connected | `connectivity` device class |
| `binary_sensor` | Data Healthy | `problem` device class (True = stale) |
| `sensor` | Serial Number, Firmware Version, Model Number, Device Type, MQTT Endpoint | Diagnostic |
| `button` | Reboot | Sends `reset: "OK"` command |

Climate features: `hvac_mode`, `preset_mode` (heat source), single/range temperature
setpoints, fan mode — with optimistic updates so the UI responds immediately.

### Requirements

```
paho-mqtt>=2.0.0
pycognito>=2023.5.1
boto3>=1.26.0
```

---

## Architecture Notes / Future Work

### MQTT broker options

The thermostat connects directly to AWS IoT Core (port 8883, mutual TLS). Two transport
options exist for the HA integration:

**Option A (current):** HA integration connects directly to AWS IoT using the
provisioned certificate. Simple, no local infrastructure required.

**Option B (local bridge):** Run a local Mosquitto broker with a bridge to AWS IoT.
HA connects to local Mosquitto (plaintext, LAN). Benefits: lower latency, offline
resilience, all control traffic stays local.

Sample Mosquitto bridge config:
```conf
connection easytouch-aws
address al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com:8883
bridge_cafile /certs/ca.pem
bridge_certfile /certs/client.crt
bridge_keyfile /certs/client.key
topic EasyTouch <serial> both 0
```

### DNS override — not feasible

DNS-overriding the AWS IoT hostname to redirect the thermostat to a local broker is
not viable. The thermostat performs full TLS server certificate verification against
Amazon Root CA 1 (baked into firmware). A local broker cannot present a certificate
signed by Amazon's CA, so the thermostat would reject the connection.

---

## File Map

```
ha-easytouch-wifi/
├── CLAUDE.md                          ← this file
├── PROTOCOL.md                        ← complete protocol reference
├── easyzone_iot_provision.py          ← AWS provisioning script
├── watch_device.py                    ← live device status watcher
├── certs/
│   ├── ca.pem                         ← Amazon Root CA 1
│   ├── client.crt                     ← provisioned IoT certificate
│   ├── client.key                     ← provisioned IoT private key
│   └── endpoint.txt                   ← AWS IoT endpoint hostname
└── custom_components/ha_easytouch_wifi/
    ├── manifest.json
    ├── strings.json
    ├── translations/en.json
    ├── const.py                       ← all constants (AWS creds, protocol, timing)
    ├── models.py                      ← ZoneConfig, ZoneState, ThermostatState
    ├── __init__.py                    ← entry setup/teardown
    ├── config_flow.py                 ← 3-step config flow with provisioning
    ├── coordinator.py                 ← paho-mqtt coordinator
    ├── climate.py                     ← climate entities (one per zone)
    ├── binary_sensor.py               ← cloud connected + data healthy
    ├── sensor.py                      ← diagnostic sensors
    └── button.py                      ← reboot button
```
