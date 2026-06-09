# EasyTouch Firmware — Static Analysis Notes

Static analysis of `EasyTouch_352_1.0.7.0.bin` (SHA-256:
`6226eeb84a75f01d414c4bfb75640bcde9dc5b21bdd57dc1f85f690a4605afa9`).

Source: the binary itself — not the Android APK. Findings here complement (and in some
cases correct) what was reverse-engineered from the app. All string offsets are into the
raw `.bin` file.

**Binary summary:**

| Field | Value |
|---|---|
| Chip | ESP32 (Xtensa LX6, 32-bit LE) |
| Flash size | 16 MB |
| Flash mode | DIO 40 MHz |
| Entry point | `0x400815ec` |
| ESP-IDF | v3.3.6-dirty |
| Build date | Nov 11 2025 15:57:26 |
| Image checksum | Valid |

---

## Undocumented MQTT fields

### Per-mode fan speed controls

The protocol doc describes a single `fan` field in `Change` commands. The firmware
(`JSON_Encoding.c`, `0x720e–0x722c`) has **five separate per-mode fan fields**, each
controlling the fan speed for a specific operating mode:

```json
{"Type": "Change", "Changes": {"zone": 0, "coolFan": 2}}
{"Type": "Change", "Changes": {"zone": 0, "eleFan": 1}}
{"Type": "Change", "Changes": {"zone": 0, "gasFan": 0}}
{"Type": "Change", "Changes": {"zone": 0, "fanOnly": 3}}
{"Type": "Change", "Changes": {"zone": 0, "autoFan": 2}}
```

| Field | Mode |
|---|---|
| `coolFan` | Cooling |
| `eleFan` | Electric heat |
| `gasFan` | Gas heat |
| `fanOnly` | Fan only |
| `autoFan` | Auto |

Values use the same encoding as the documented `fan` field: `0`=Auto, `1`=Low,
`2`=Medium, `3`=High.

The generic `fan` field from the protocol doc likely sets all modes at once; these fields
set per-mode speeds individually.

---

### `power` field in `Change`

At `0x7234`, immediately after the per-mode fan fields:

```json
{"Type": "Change", "Changes": {"zone": 0, "power": <value>}}
```

Distinct from `mode: 0` (documented power-off). Likely a direct on/off toggle that
preserves the stored mode rather than overwriting it.

---

### `push` — enable/disable push notifications

At `0x725c`:

```json
{"Type": "Change", "Changes": {"push": 1}}
{"Type": "Change", "Changes": {"push": 0}}
```

Sets the `pushNotifyEnabled` flag (bit 2 of `PRM[1]` in status responses). The protocol
doc documents the flag in status but not the command that sets it.

---

### `PWD` — Bluetooth PIN

At `0x7261`:

```json
{"Type": "Change", "Changes": {"PWD": "<pin string>"}}
```

Sets the Bluetooth pairing PIN. The device stores it in NVS under the key `bt_password`
(`0x6152`). No prior documentation of this field.

---

### `PushNotify` — device-originated alert message

At `0x5e74–0x5e87`, in the AWS IoT task main loop:

```
'UPDATE'
'PushNotify'
... log: "Sending Alert Bytes: %d"
```

The device **publishes** a `PushNotify` message on the shared `EasyTouch <serial>` topic
when a temperature alert fires (alertLL / alertUL threshold crossed). This is not a
command the app sends — it is an outbound push from the device.

The protocol doc covers the threshold configuration but does not document this outbound
message type. Expected shape (inferred from context):

```json
{"Type": "PushNotify", "SN": "352016109", "Zone": 0}
```

Exact payload fields require live capture to confirm.

---

## `UPDATE` command — full structure

`FIRMWARE_UPDATES.md` already documents the DynamoDB-to-MQTT flow. The firmware adds
detail on all recognised fields and the abort codes the device logs (and likely returns).

### `Action: "FW"` — firmware OTA

```json
{
  "Type": "UPDATE",
  "Action": "FW",
  "Device Type": "352",
  "Serial": "352016364",
  "Revision": "1.0.8.0",
  "RevCheckMode": "Off",
  "SerialCheckMode": "Match",
  "Web Server": "s3.us-east-1.amazonaws.com",
  "Web URL": "https://…/EasyTouch_352_1.0.8.0.bin",
  "Port": "443"
}
```

### `Action: "FS"` — SPIFFS filesystem update

Not mentioned in FIRMWARE_UPDATES.md. Patches the SPIFFS partition (fonts, images)
without reflashing the app binary.

```json
{
  "Type": "UPDATE",
  "Action": "FS",
  "Device Type": "352",
  "Serial": "352016364",
  "Revision": "1.0.7.0",
  "RevCheckMode": "Off",
  "SerialCheckMode": "Match",
  "Folder Directory": "<remote path>",
  "Web Server": "s3.us-east-1.amazonaws.com",
  "Web URL": "https://…/",
  "Port": "443"
}
```

`Folder Directory` replaces `Web URL` for filesystem updates.

### Abort codes

The firmware logs a short result code for each outcome (`0x72bf–0x75e0`). These codes are
likely included in any response message sent back before aborting.

| Code | Condition |
|---|---|
| `FW1` / `PASS` | Serial matched — OTA started |
| `FW2` | Null/missing fields in UPDATE message |
| `FW3` | `Device Type` or serial doesn't match this unit |
| `FW4` | Unrecognised `SerialCheckMode` value |
| `FW5` | Revision identical to installed firmware |
| `FW6` | Serial number mismatch |
| `FS1` | Serial matched — filesystem update started |
| `FS2` | Unrecognised `SerialCheckMode` value |
| `FS3` | Firmware revision difference detected (treated as warning, not abort) |
| `FS4`–`FS6` | Analogous to FW3–FW5 for filesystem path |

`RevCheckMode: "Off"` skips the version check (FW5 abort). `SerialCheckMode: "Match"`
is the only confirmed valid value; any other string triggers FW4/FS2.

---

## Observations about documented commands

### `Get Modes` — not present in firmware

The string `Get Modes` does not appear anywhere in the binary. The command is documented
in PROTOCOL.md (sourced from APK decompile), but this firmware version either does not
handle it or uses a different dispatch path. The mode availability data is delivered in
the `Get Config` response (`MAV` field) regardless, so this may be vestigial.

### `latitude` / `longitude` alongside `LAT` / `LON`

At `0x6ff1–0x7002`, the firmware has both the short (`LAT`, `LON`) and long
(`latitude`, `longitude`) forms as separate string constants. Both are in the
`ExtraData` handler context. The device may accept either form in incoming messages, or
emit the long form in some responses.

### `SS` and `OS` fields in `ExtraData` response

The protocol doc notes `SS` and `OS` as "unknown — observed values: 27, 0". These keys
do not appear as null-terminated strings in the binary, which means they are either:

- Assembled from inline character literals (common in embedded JSON builders that avoid
  full string allocation), or
- Named differently internally and the field names observed in traffic come from a
  different code path not covered by this analysis.

Their meaning remains unknown.

### `ambient` as a top-level field

`ambient` (`0x709e`) appears in the `JSON_Encoding.c` string cluster between `hA` and
the per-zone mode strings. It may be an additional top-level temperature field in Status
responses (alongside the per-zone `Z_sts[12]` ambient value), or a field in a
non-documented diagnostic message type.

---

## NVS storage keys

Not MQTT fields, but useful for understanding device state and configuration persistence.
All stored in the `storage` NVS namespace.

| Key | Description |
|---|---|
| `bt_password` | Bluetooth pairing PIN (set via `PWD` Change command) |
| `TZ` | Timezone string; default `EST+5` |
| `silentBoot` | Suppress startup display/sounds if set |
| `AlertMin` / `AlertMax` | Legacy NVS names for `alertLL` / `alertUL`; read during old-config migration |
| `ts_minx` / `ts_maxx` / `ts_miny` / `ts_maxy` / `ts_conx` / `ts_cony` | Touchscreen calibration |
| `s_bright` | Display brightness |
| `s_Offset` / `s_parm` / `s_parmA` / `s_DZ` | Display tuning parameters |
| `h_offset` | Horizontal display offset |
| `options` | Packed display/behaviour flags |
| `Sys_Parms` / `Grp2Parm` | System parameter blobs (old config format) |

---

## SPIFFS filesystem layout

The device uses a SPIFFS partition mounted at `/spiffs`. Observed paths:

```
/spiffs/fonts/pem.crt      — device certificate (mutual TLS)
/spiffs/fonts/pem.key      — device private key
/spiffs/fonts/root.pem     — Amazon Root CA
/spiffs/fonts/svrrt.pem    — server certificate
/spiffs/images/Micro-Air_logo.jpg
/spiffs/images/Micro-Air_logo.bmp
```

UI image assets (`.jpg`) are also stored in SPIFFS and updated via `Action: "FS"`.

---

## Analysis tooling

```bash
# Image metadata
esptool image-info EasyTouch_352_1.0.7.0.bin

# ELF for radare2 (all 6 segments at correct load addresses)
python3 build_elf.py          # produces EasyTouch_352_1.0.7.0.elf

# Disassemble (Xtensa LX6)
r2 -a xtensa -e cfg.bigendian=false EasyTouch_352_1.0.7.0.elf
```

Scripts `build_elf.py` and `extract_segments.py` live alongside the `.bin` in the
analysis working directory.
