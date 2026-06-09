"""Constants for the EasyTouch Wi-Fi integration."""

DOMAIN = "ha_easytouch_wifi"

# ── Config entry keys ─────────────────────────────────────────────────────────
CONF_USERNAME = "username"
CONF_SERIAL = "serial"
CONF_CA_PEM = "ca_pem"
CONF_CLIENT_CERT = "client_cert"
CONF_CLIENT_KEY = "client_key"
CONF_MQTT_ENDPOINT = "mqtt_endpoint"

# ── AWS / Cognito constants ───────────────────────────────────────────────────
REGION = "us-east-1"
USER_POOL_ID = "us-east-1_M9Hs9kugG"
APP_CLIENT_ID = "2mfujqa7vidd2td9h08k9m061s"
APP_CLIENT_SECRET = "bbq2v6edtn25j0pgdus7tsi4fq93rgiqfq0g0ener8m896o2euu"
IDENTITY_POOL_ID = "us-east-1:527ec272-ac1b-4301-8e46-464669790d6e"
IOT_POLICY_NAME = "MyAndroidPolicy"
IOT_ENDPOINT_FALLBACK = "al2tvpwq2i0lf-ats.iot.us-east-1.amazonaws.com"

# Amazon Root CA 1 — public certificate, embedded for convenience.
# See https://www.amazontrust.com/repository/
AMAZON_ROOT_CA_1 = """\
-----BEGIN CERTIFICATE-----
MIIDQTCCAimgAwIBAgITBmyfz5m/jAo54vB4ikPmljZbyjANBgkqhkiG9w0BAQsF
ADA5MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6
b24gUm9vdCBDQSAxMB4XDTE1MDUyNjAwMDAwMFoXDTM4MDExNzAwMDAwMFowOTEL
MAkGA1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJv
b3QgQ0EgMTCCASIwDQYJKoIIhvcNAQEBBQADggEPADCCAQoCggEBALJ4gHHKeNXj
ca9HgFB0fW7Y14h29Jlo91ghYPl0hAEvrAIthtOgQ3pOsqTQNroBvo3bSMgHFzZM
9O6II8c+6zf1tRn4SWiw3te5djgdYZ6k/oI2peVKVuRF4fn9tBb6dNqcmzU5L/qw
IFAGbHrQgLKm+a/sRxmPUDgH3KKHOVj4utWp+UhnMJbulHheb4mjUcAwhmahRWa6
VOujw5H5SNz/0egwLX0tdHA114gk957EWW67c4cX8jJGKLhD+rcdqsq08p8kDi1L
93FcXmn/6pUCyziKrlA4b9v7LWIbxcceVOF34GfID5yHI9Y/QCB/IIDEgEw+OyQm
jgSubJrIqg0CAwEAAaNCMEAwDwYDVR0TAQH/BAUwAwEB/zAOBgNVHQ8BAf8EBAMC
AYYwHQYDVR0OBBYEFIQYzIU07LwMlJQuCFmcx7IQTgoIMA0GCSqGSIb3DQEBCwUA
A4IBAQCY8jdaQZChGsV2USggNiMOruYou6r4lK5IpDB/G/wkjUu0yKGX9rbxenDI
U5PMCCjjmCXPI6T53iHTfIUJrU6adTrCC2qJeHZERxhlbI1Bjjt/msv0tadQ1wUs
N+gDS63pYaACbvXy8MWy7Vu33PqUXHeeE6V/Uq2V8viTO96LXFvKWlJbYK8U90v
vo/ufQJVtMVT8QtPHRh8jrdkPSHCa2XV4cdFyQzR1bldZwgJcJmApzyMZXo6b5U4
5UOp9g9A4YT0Vz+cLLb1Oc7Xc1YPCkiqBP/F4SZMPQ2LLvPFf2KFZAJ2Yzqhex
BsN0tLYLXG7SN3jqUzJFmMnUm2vE
-----END CERTIFICATE-----
"""

# ── MQTT transport ────────────────────────────────────────────────────────────
MQTT_PORT = 8883
MQTT_TOPIC_FORMAT = "EasyTouch {serial}"

# ── Device mode numbers ───────────────────────────────────────────────────────
MODE_OFF = 0
MODE_FAN_ONLY = 1
MODE_COOL = 2
MODE_HEAT = 3           # Generic heat (gas fan)
MODE_FURNACE = 4        # AquaHot / Diesel furnace
MODE_HEAT_PUMP = 5      # Heat pump (electric fan)
MODE_DRY = 6
MODE_HEAT_STRIP = 7     # Heat strip (electric fan)
MODE_AUTO = 8
MODE_AUTO_HS = 9        # Auto with heat strip backup
MODE_AUTO_HP = 10       # Auto with heat pump backup
MODE_AUTO_FURNACE = 11  # Auto with furnace backup
MODE_ELECTRIC_HEAT = 12
MODE_GAS_HEAT = 13      # Gas heat

# Furnace / gas modes whose fan is autonomous (always "auto" in HA)
GAS_MODES: frozenset[int] = frozenset({MODE_HEAT, MODE_FURNACE, MODE_GAS_HEAT})

# ── HA mode mappings ──────────────────────────────────────────────────────────
DEVICE_TO_HA_MODE: dict[int, str] = {
    MODE_OFF: "off",
    MODE_FAN_ONLY: "fan_only",
    MODE_COOL: "cool",
    MODE_HEAT: "heat",
    MODE_FURNACE: "heat",
    MODE_HEAT_PUMP: "heat",
    MODE_DRY: "dry",
    MODE_HEAT_STRIP: "heat",
    MODE_AUTO: "auto",
    MODE_AUTO_HS: "auto",
    MODE_AUTO_HP: "auto",
    MODE_AUTO_FURNACE: "auto",
    MODE_ELECTRIC_HEAT: "heat",
    MODE_GAS_HEAT: "heat",
}

HA_TO_DEVICE_DEFAULT: dict[str, int] = {
    "off": MODE_OFF,
    "fan_only": MODE_FAN_ONLY,
    "cool": MODE_COOL,
    "heat": MODE_HEAT_PUMP,
    "dry": MODE_DRY,
    "auto": MODE_AUTO,
}

# ── Heat-source preset modes ──────────────────────────────────────────────────
HEAT_TYPE_PRESETS: dict[str, int] = {
    "Heat Pump": MODE_HEAT_PUMP,
    "Furnace": MODE_FURNACE,
    "Heat Strip": MODE_HEAT_STRIP,
    "Electric Heat": MODE_ELECTRIC_HEAT,
    "Gas Heat": MODE_GAS_HEAT,
    "Heat": MODE_HEAT,
}
HEAT_TYPE_REVERSE: dict[int, str] = {v: k for k, v in HEAT_TYPE_PRESETS.items()}

# ── Fan speed values (Z_sts indices 6-9, 11) ─────────────────────────────────
# Device sends: 0=Auto, 1=Low, 2=Med, 3=High, 128=N/A (treated as Auto)
FAN_VALUE_TO_HA: dict[int, str] = {
    0: "auto",
    1: "low",
    2: "high",
    3: "high",    # some units have 3-speed fans
    128: "auto",  # N/A treated as auto
}

HA_FAN_TO_VALUE: dict[str, int] = {
    "auto": 128,
    "low": 1,
    "high": 2,
}

# Fan field names in Change command (mode-dependent, same as BLE)
FAN_FIELD_ELECTRIC = "eleFan"    # heat_pump, heat_strip, electric_heat
FAN_FIELD_GAS = "gasFan"         # heat, furnace, gas_heat
FAN_FIELD_COOL = "coolFan"       # cool
FAN_FIELD_AUTO = "autoFan"       # auto
FAN_FIELD_FAN_ONLY = "fanOnly"   # fan_only

# ── Z_sts array indices ───────────────────────────────────────────────────────
IDX_AUTO_HEAT_SP = 0
IDX_AUTO_COOL_SP = 1
IDX_COOL_SP = 2
IDX_HEAT_SP = 3
IDX_DRY_SP = 4
IDX_FAN_ONLY_SPEED = 6
IDX_COOL_FAN_SPEED = 7
IDX_ELECTRIC_FAN_SPEED = 8
IDX_AUTO_FAN_SPEED = 9
IDX_MODE = 10
IDX_GAS_FAN_SPEED = 11
IDX_AMBIENT_TEMP = 12
IDX_FAULT = 14
IDX_STATUS_FLAGS = 15

# STATUS_FLAGS bitmask (Z_sts[15])
FLAG_CYCLE_ACTIVE = 0x01
FLAG_IS_COOLING = 0x02
FLAG_IS_HEATING = 0x04

# PRM[1] bitmask
PRM_FLAG_WIFI_CONNECTED  = 0x01
PRM_FLAG_AWS_CONNECTED   = 0x02
PRM_FLAG_PUSH_NOTIFY     = 0x04
PRM_FLAG_SYSTEM_POWER    = 0x08

# ── Timing ────────────────────────────────────────────────────────────────────
MQTT_POLL_INTERVAL_S = 10.0        # Status poll interval
CONFIG_REQUEST_DELAY_S = 0.3       # Delay between Get Config zone requests
DEBOUNCE_DELAY_S = 0.5             # Debounce for temperature / fan changes
STATUS_SUPPRESS_S = 5.0            # Suppress status updates after a command
STALE_TIMEOUT_S = 60.0             # Mark data stale if no update within this time

# Reconnect backoff
RECONNECT_BACKOFF_BASE_S = 5
RECONNECT_BACKOFF_CAP_S = 120
RECONNECT_MAX_FAILURES = 20

# ── Temperature defaults (°F) ─────────────────────────────────────────────────
DEFAULT_MIN_TEMP = 60
DEFAULT_MAX_TEMP = 90
TEMP_STEP = 1.0
