# EasyTouch Firmware Update Mechanism

Reverse-engineered from the Android app (`CheckUpdates.java`, `AWS_Service.java`,
`DynamoUpdates.java`) and validated by live DynamoDB queries.

---

## Overview

Firmware updates involve three separate systems:

1. **AWS DynamoDB** — stores the latest available firmware version and download URL
2. **AWS MQTT** — the app sends an update instruction to the device
3. **HTTP (device-side)** — the device downloads the firmware binary autonomously and reboots

The app never downloads the binary itself. It only reads the metadata and tells the device where to fetch from.

---

## Step 1 — Check for available firmware (DynamoDB)

### Authentication

DynamoDB access requires authenticated Cognito Identity Pool credentials — unauthenticated
(guest) access is **not supported** by this pool.

Flow:
1. Cognito SRP auth (`USER_SRP_AUTH`) → ID token
2. `cognito-identity.get_id()` with the ID token → Identity ID
3. `cognito-identity.get_credentials_for_identity()` → temporary AWS credentials
4. Use those credentials for the DynamoDB `get_item` call

> **Refresh token note:** The `REFRESH_TOKEN_AUTH` flow requires a `SECRET_HASH` computed
> as `HMAC-SHA256(ClientSecret, username + ClientId)`. As of testing (2026-06-09), this
> flow returns `NotAuthorizedException` even with a correctly-computed hash. Root cause
> unknown — may be a pool configuration restriction. Workaround: re-authenticate with
> full SRP on each check.

### Tables

| Table | Purpose |
|---|---|
| `Updates` | Production firmware |
| `BetaUpdates` | Beta/pre-release firmware |

Region: `us-east-1`

### Query

```python
dynamo.get_item(
    TableName="Updates",
    Key={"Device_type": {"S": model_number}},  # e.g. "352"
)
```

`model_number` = first 3 characters of the device serial number.

### Record structure

| DynamoDB field | Type | Example | Description |
|---|---|---|---|
| `Device_type` | String (hash key) | `"352"` | Device model prefix |
| `Action` | String | `"FW"` | Update type (always `"FW"` for firmware) |
| `Revision` | String | `"1.0.7.0"` | Latest available firmware version |
| `RevCheckMode` | String | `"Off"` | Version check mode flag |
| `WebServer` | String | `"s3.us-east-1.amazonaws.com"` | Firmware download hostname |
| `WebURL` | String | `"https://…/EasyTouch_352_1.0.7.0.bin"` | Full firmware download URL |
| `Port` | String | `"443"` | Download port |

### Live data (queried 2026-06-09, model 352)

**Updates (production):**
```
Revision    1.0.7.0
WebURL      https://microaireasytouchrvupdates.s3.us-east-1.amazonaws.com/352/bin/1.0.7.0/EasyTouch_352_1.0.7.0.bin
Port        443
Action      FW
RevCheckMode Off
```

**BetaUpdates:**
```
Revision    0.0.6.15
WebURL      https://microaireasytouchrvbetaupdates.s3.amazonaws.com/352/bin/1.0.6.15/EasyTouch_352_1.0.6.15.bin
```

### Version comparison

The Android app compares using 4-component semantic versioning (`MAJOR.MINOR.PATCH.BUILD`).
A build number of `99` in any component is treated as a development/pre-release build.
An update is available when any component of the available version is greater than the
installed version (compared left-to-right, stopping at first difference).

The installed version is reported by the device in every status response as the `REV` field.

---

## Step 2 — Trigger firmware update (MQTT)

When the user confirms the update, the app publishes a single MQTT message to the device
topic (`EasyTouch <serial>`). The device then handles the download, flash, and reboot
entirely on its own.

### MQTT UPDATE message

```json
{
  "Type": "UPDATE",
  "Action": "<DynamoDB Action>",
  "Device Type": "<DynamoDB Device_type>",
  "Serial": "<device serial number>",
  "Revision": "<DynamoDB Revision>",
  "RevCheckMode": "<DynamoDB RevCheckMode>",
  "SerialCheckMode": "Match",
  "Web Server": "<DynamoDB WebServer>",
  "Web URL": "<DynamoDB WebURL>",
  "Port": "<DynamoDB Port>"
}
```

**Concrete example (model 352, firmware 1.0.7.0):**
```json
{
  "Type": "UPDATE",
  "Action": "FW",
  "Device Type": "352",
  "Serial": "352016364",
  "Revision": "1.0.7.0",
  "RevCheckMode": "Off",
  "SerialCheckMode": "Match",
  "Web Server": "s3.us-east-1.amazonaws.com",
  "Web URL": "https://microaireasytouchrvupdates.s3.us-east-1.amazonaws.com/352/bin/1.0.7.0/EasyTouch_352_1.0.7.0.bin",
  "Port": "443"
}
```

Field mapping: all fields come directly from the DynamoDB record except `"Serial"`
(from the device) and `"SerialCheckMode"` (hardcoded `"Match"`).

### Device behaviour after receiving UPDATE

- Device downloads the binary from `Web URL` autonomously
- Device flashes the new firmware
- Device reboots — MQTT connection drops during this process
- No explicit response is sent back before the reboot
- The app shows: *"Update has been sent. Your device will now reset after the update is
  complete. This app will disconnect from the device during the process."*

---

## Source files

| File | Relevance |
|---|---|
| `com/microair/android/easyzone_rv/Control_Settings/CheckUpdates.java` | Version comparison, MQTT payload construction |
| `com/microair/android/easyzone_rv/AWS/AWS_Service.java` | DynamoDB query via `readDynamoUpdateInfo()` |
| `com/amazonaws/models/nosql/DynamoUpdates.java` | DynamoDB model (production) |
| `com/amazonaws/models/nosql/DynamoBetaUpdates.java` | DynamoDB model (beta) |
