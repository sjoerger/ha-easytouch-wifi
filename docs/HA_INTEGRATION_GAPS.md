# EasyTouch — Home Assistant Integration Gaps

Analysis of `ha-easytouch-wifi` (MQTT/WiFi) and `ha-easytouch` (BLE) against firmware
`EasyTouch_352_1.0.7.0.bin` and both APK decompiles. Items are ordered by implementation
effort, not severity.

---

## Tier 1 — Easy wins (data already in-hand)

### Fault code sensor — not exposed in either integration

Both integrations parse `Z_sts[14]` into a `fault: int` field in the zone model, but
neither creates a HA entity from it. A `sensor` with `device_class: enum` and
human-readable state would surface faults without requiring the user to look at raw
diagnostics.

The firmware UI strings (`0x14579–0x14747`) reveal the complete fault table — and it
differs from PROTOCOL.md in several places (see fault table corrections below). The full
table from the binary:

| Code | Firmware display string | PROTOCOL.md (APK-sourced) |
|---|---|---|
| 0 | *(no fault)* | No fault |
| 1 | BAD ROOM SENSOR | No communication |
| 2 | BAD OUTSIDE SENSOR | Bad remote sensor |
| 3 | BAD FREEZE SENSOR | Bad outside sensor |
| 4 | FREEZE DETECTED | Bad freeze sensor |
| 5 | BAD HUMID SENSOR | Freeze detected |
| 6 | NO AC POWER | Bad humidity sensor |
| 7 | INVALID CONFIG | No AC power |
| 8 | LOW DC VOLTAGE | Invalid config |
| 9 | BAD INDOOR SENSOR | Low DC voltage |
| 10 | INSIDE COIL SENSOR | Bad indoor sensor |
| 11 | OUTDOOR COIL SENSOR | Inside coil sensor fault |
| 12 | HP OVER TEMP | Outside coil sensor fault ← wrong |
| 13 | OVERRIDE ACTIVE | Low refrigerant ← wrong |
| 14 | LOW REFRIGERANT | Undefined fault ← wrong |
| 15 | COMPRESSOR OVERLOAD | *(undocumented)* |
| 16–23 | FAULT1–FAULT8 | *(undocumented)* |

Codes 12–23 are wrong or missing from the APK-derived table. PROTOCOL.md should be
updated. Both integrations should use the firmware-sourced table.

**Implementation:** add a `sensor` entity per zone with `native_value` being the fault
name string and `state_class = None`. `icon` can be `mdi:alert-circle` when non-zero.

---

### Weather temperature sensor — already in every status response

`PRM[2]` = `weatherTemperature` is included in every Status response. It is the current
outside temperature fetched by the device from OpenWeatherMap (the device pulls this
autonomously using the `LAT`/`LON` the app sends). It is not exposed by either
integration.

This is a free sensor — the value is already being received and discarded.

**Implementation:** add a `sensor` entity from `device_state.prm[2]`. Skip/show
unavailable when the value is `128` (sentinel) or `255`.

---

### Outside air temperature sensor — already in zone status

`Z_sts[13]` = `outsideAirTemp` is an optional hardware outdoor sensor reading reported
per-zone. `255` means the sensor is not present. Neither integration exposes this.

Like the weather temperature, this value is already arriving in every status poll.

**Implementation:** add a `sensor` entity per zone with `native_value = Z_sts[13]`,
hidden by default (`entity_registry_enabled_default=False`) since the sensor hardware is
optional.

---

### Alert thresholds — missing from BLE integration

`ha-easytouch-wifi` exposes `alertLL` / `alertUL` as `number` entities with the 2°F gap
constraint enforced. `ha-easytouch` (BLE) does not.

The BLE Change command payload is identical to MQTT — sending
`{"Type": "Change", "Changes": {"zone": 0, "alertLL": 45}}` over BLE characteristic
`0xEE01` works the same way. The BLE integration already has the `Changes` send path; the
gap is only on the entity side.

**Implementation:** copy the `number.py` entities from the WiFi integration into BLE,
using the coordinator's existing `send_change` method.

---

### Push notification switch — undocumented field, trivial to add

The `push` field in a `Change` payload (`0x725c` in the binary) sets bit 2 of `PRM[1]`
(`pushNotifyEnabled`). Neither integration exposes a control for it, though the WiFi
integration reads and exposes the current state via the `data_healthy` binary sensor
indirectly through the PRM flags.

**Implementation:** a `switch` entity that reads `device_state.prm_flags.push_notify`
and sends `{"Type": "Change", "Changes": {"push": 1}}` / `{"push": 0}` to toggle.

---

## Tier 2 — Medium effort

### `hA` home/away mode — fully implemented in firmware, not exposed

The device has a complete home/away implementation:

- MQTT: `hA` field in Status responses (`0x709b`); `{"Type": "Change", "Changes": {"zone": 0, "hA": 1}}` to set
- NVS: persisted under key `s_away` (`0x5115`) — survives reboots
- UI: dedicated AWAY / HOME buttons (`0x14b12–0x14b17`) in the setup menu
- Status: `hA` is a top-level Status response field (not per-zone)
- PROTOCOL.md notes: `hA: 0` = home, `1` = away; only present in response if device supports it

The `hA` field is already parsed in the WiFi coordinator (`obj.get("hA")`) but only to
check feature presence, not to expose a controllable entity.

**Implementation options:**
- `select` entity with options `["home", "away"]` — simplest, maps directly to `hA: 0/1`
- Climate `preset_mode` — integrate with the climate entity (complicates the preset list)

The `select` entity is cleaner since `hA` is device-wide, not per-zone.

---

### ~~Fan speed mapping — medium speed missing, high speed may be wrong~~ (Resolved)

The BLE integration confirmed the full fan speed encoding:

| Value | HA mode | Meaning |
|---|---|---|
| 0 | auto | Auto |
| 1 | Low | Manual Low (continuous) |
| 2 | High | Manual High (continuous) |
| 3 | High | Manual High — 3-speed units only |
| 65 | Cycled Low | Fan runs only during active cycle, low speed |
| 66 | Cycled High | Fan runs only during active cycle, high speed |
| 128 | auto | N/A (treated as Auto) |

The Wi-Fi integration now exposes all five user-selectable modes: **Auto, Low, High, Cycled Low, Cycled High**. Gas/furnace modes remain auto-only (fan is autonomous). The FA bitmask from the Config response is no longer used for fan mode filtering — modes are hardcoded since all units support the same set.

---

### Night / sleep mode — display timeout toggle, not an HVAC feature

The Day/Night toggle in Settings controls whether the faceplate display stays on
permanently (Day) or turns off after ~30 seconds of inactivity (Night). It has no
effect on HVAC operation.

The firmware strings confirm this: `ACTIVE LEVEL` and `SLEEP LEVEL` (`0x14960–0x1496d`)
are the two display brightness values, and `mode_sleep.jpg` / `mode_sleep_off.jpg` are
the toggle button icons. The setting is stored locally in NVS (likely `s_SSB` or the
`options` key at `0x50fe`/`0x5021`) and has no MQTT representation — it does not appear
anywhere in the JSON encoding region.

**Not relevant for HA integration.**

---

### Schedule support

The schedule system is fully implemented in the firmware:

- `Get Schedule` is in the command dispatch table (`0x767d`)
- `{"Type": "Get Schedule", "Day": 0}` → `{"Type": "Response", "RT": "Schedule", ...}` (documented in PROTOCOL.md)
- NVS keys `day0`–`day6` and `day0A`–`day6A` persist the schedule locally
- The SCHEDULE menu is accessible from the device faceplate
- Both coordinators acknowledge `RT: "Schedule"` responses without parsing them

PROTOCOL.md documents the full Schedule request/response format including the 5-element
slot encoding. The data is available; it just isn't wired to any HA entity.

**Implementation options:**
- HA `calendar` entity (complex; requires bidirectional sync)
- Template or script helpers that call the coordinator's send path directly
- Expose raw schedule as a `sensor` whose state is the JSON blob (diagnostic only)

The schedule is the most complex feature gap but also the most user-visible: the device
faceplate lets users set programs, but there is no way to read or write them from HA.

---

## Tier 3 — Context and corrections (no new entities needed)

### Hysteresis / differential gap — device-local setting

The device has a configurable temperature differential (deadband) for heat and cool,
stored in NVS as `s_coolhyst` and `s_heathyst` (`0x51ae–0x51b9`). The faceplate UI
("TEMPERATURE DIFFERENTIAL GAP", `0x149c7`) shows an example:

> Heat Setpoint: 20.0°C / Heat Gap: 1.6° / Turns on at 18.3° / Off at 20.5°

This setting is **not accessible via MQTT** — it is faceplate-only. However it affects
how the climate entity behaves: the compressor cycles relative to a setpoint ± hysteresis,
not exactly at the setpoint. HA users may wonder why the unit doesn't respond at exactly
the target temperature; the answer is this gap.

No HA entity is needed, but a note in the integration documentation would help.

---

### Gas assist active state

The faceplate shows `Gas Assist Active` / `Gas Assist Off` (`0x14b67`). This is a
supplemental gas heating status for AquaHot / hydronic systems. The state is not
visible in any protocol field we can identify — it may be encoded in `Z_sts[15]`
status flags at a bit position not yet decoded.

**Next step:** observe `Z_sts[15]` while gas assist is active to identify the bit.

---

### `email_address` NVS key

The device stores an email address in NVS (`0x511c`). This is likely the destination
for server-side alert emails triggered by the push notification system. Not accessible
via MQTT, not relevant for HA entity creation, but explains the purpose of `PushNotify`
messages — they likely trigger an email from the AWS IoT backend in addition to the
mobile app push.

---

## Summary table

| Feature | WiFi | BLE | Effort | Priority |
|---|---|---|---|---|
| Fault code sensor (corrected table) | ✗ | ✗ | Low | High |
| Weather temp sensor (`PRM[2]`) | ✗ | ✗ | Low | High |
| Outside air temp sensor (`Z_sts[13]`) | ✗ | ✗ | Low | Medium |
| Alert thresholds | ✓ | ✗ | Low | Medium |
| Push notification switch | ✗ | ✗ | Low | Low |
| Home/away select (`hA`) | ✗ | ✗ | Medium | High |
| Fan speed mapping (all 5 modes) | ✓ | Bug | — | — |
| Night mode (investigation needed) | ✗ | ✗ | Unknown | Medium |
| Schedule (read/write) | ✗ | ✗ | High | Medium |
| Hysteresis (doc only, not MQTT) | — | — | Doc only | Low |
