"""Coordinator for EasyTouch Wi-Fi MQTT communication.

Transport: AWS IoT Core MQTT (port 8883, mutual TLS).

Protocol:
  - Both publish and subscribe use topic: "EasyTouch <serial>"
  - App → device: JSON with "Type" field
  - Device → app: JSON with Type="Response" and RT field

Session flow:
  1. Connect with TLS client certificate
  2. Subscribe to "EasyTouch <serial>"
  3. Request Get Config for zones 0-3
  4. Poll Get Status every MQTT_POLL_INTERVAL_S seconds

paho-mqtt runs its network loop in a background thread (loop_start).
Callbacks bridge to the HA event loop via hass.loop.call_soon_threadsafe.
Commands are published directly from the HA event loop (paho publish is thread-safe).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import tempfile
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CA_PEM,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_MQTT_ENDPOINT,
    CONF_SERIAL,
    CONFIG_REQUEST_DELAY_S,
    DEBOUNCE_DELAY_S,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEVICE_TO_HA_MODE,
    DOMAIN,
    FAN_FIELD_AUTO,
    FAN_FIELD_COOL,
    FAN_FIELD_ELECTRIC,
    FAN_FIELD_FAN_ONLY,
    FAN_FIELD_GAS,
    FAN_VALUE_TO_HA,
    GAS_MODES,
    HA_FAN_TO_VALUE,
    HA_TO_DEVICE_DEFAULT,
    HEAT_TYPE_PRESETS,
    IDX_AMBIENT_TEMP,
    IDX_AUTO_COOL_SP,
    IDX_AUTO_FAN_SPEED,
    IDX_AUTO_HEAT_SP,
    IDX_COOL_FAN_SPEED,
    IDX_COOL_SP,
    IDX_DRY_SP,
    IDX_ELECTRIC_FAN_SPEED,
    IDX_FAULT,
    IDX_FAN_ONLY_SPEED,
    IDX_GAS_FAN_SPEED,
    IDX_HEAT_SP,
    IDX_MODE,
    IDX_STATUS_FLAGS,
    MQTT_POLL_INTERVAL_S,
    MQTT_PORT,
    MQTT_TOPIC_FORMAT,
    PRM_FLAG_SYSTEM_POWER,
    FLAG_CYCLE_ACTIVE,
    FLAG_IS_COOLING,
    FLAG_IS_HEATING,
    RECONNECT_BACKOFF_BASE_S,
    RECONNECT_BACKOFF_CAP_S,
    RECONNECT_MAX_FAILURES,
    STALE_TIMEOUT_S,
    STATUS_SUPPRESS_S,
)
from .models import ThermostatState, ZoneConfig, ZoneState

_LOGGER = logging.getLogger(__name__)


def _fan_field_for_mode(mode_num: int) -> str:
    """Return the JSON fan-field name for the given device mode number."""
    if mode_num in (5, 7, 12):   # heat_pump, heat_strip, electric_heat
        return FAN_FIELD_ELECTRIC
    if mode_num in (3, 4, 13):   # heat, furnace, gas_heat
        return FAN_FIELD_GAS
    return FAN_FIELD_COOL


def _build_ssl_context(client_cert: str, client_key: str) -> ssl.SSLContext:
    """Build a mutual-TLS SSL context using the system CA bundle.

    The system CA bundle (which includes Amazon Root CA 1) is used for server
    verification. Client cert/key are written to temporary files and deleted
    immediately after loading.
    """
    ctx = ssl.create_default_context()

    tmp_cert = tmp_key = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(client_cert)
            tmp_cert = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(client_key)
            tmp_key = f.name
        ctx.load_cert_chain(tmp_cert, tmp_key)
    finally:
        for p in (tmp_cert, tmp_key):
            if p and os.path.exists(p):
                os.unlink(p)

    return ctx


class EasyTouchMQTTCoordinator(DataUpdateCoordinator[ThermostatState | None]):
    """Coordinate MQTT communication with one EasyTouch thermostat."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.unique_id}",
            update_interval=None,  # push-based
        )
        self.entry = entry
        self._serial: str = entry.data[CONF_SERIAL]
        self._topic: str = MQTT_TOPIC_FORMAT.format(serial=self._serial)
        self._ca_pem: str = entry.data[CONF_CA_PEM]
        self._client_cert: str = entry.data[CONF_CLIENT_CERT]
        self._client_key: str = entry.data[CONF_CLIENT_KEY]
        self._endpoint: str = entry.data[CONF_MQTT_ENDPOINT]

        # paho client (created in async_start)
        self._mqtt = None
        self._connected = False

        # Zone configuration and live state
        self.zone_configs: dict[int, ZoneConfig] = {}
        self.thermostat_state: ThermostatState | None = None

        # Device info populated from status responses
        self.serial_number: str | None = None
        self.firmware_version: str | None = None
        self.device_type: str | None = None
        self.config_index: str | None = None
        self.device_model: str | None = None

        # Lifecycle
        self._poll_task: asyncio.Task | None = None
        self._config_done = False
        self._connected_event = asyncio.Event()
        self._consecutive_failures = 0
        self._last_data_time: float = 0.0

        # Post-command suppression
        self._suppress_until: float = 0.0

        # Debounce tasks (temperature/fan)
        self._debounce_tasks: dict[str, asyncio.Task] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Public state helpers
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def data_healthy(self) -> bool:
        if self._last_data_time == 0.0:
            return False
        return (time.monotonic() - self._last_data_time) < STALE_TIMEOUT_S

    def is_status_suppressed(self) -> bool:
        return time.monotonic() < self._suppress_until

    def suppress_status(self, seconds: float = STATUS_SUPPRESS_S) -> None:
        self._suppress_until = time.monotonic() + seconds

    def get_available_hvac_modes(self, zone: int) -> list[str]:
        """Return HA HVAC modes available for a zone (from MAV bitmask)."""
        cfg = self.zone_configs.get(zone)
        if cfg is None or cfg.available_modes_mask == 0:
            return ["off", "heat", "cool", "auto", "fan_only", "dry"]
        mav = cfg.available_modes_mask
        modes = ["off"]
        seen: set[str] = {"off"}
        for bit in range(16):
            if mav & (1 << bit):
                ha = DEVICE_TO_HA_MODE.get(bit)
                if ha and ha not in seen:
                    modes.append(ha)
                    seen.add(ha)
        return modes

    def get_available_presets(self, zone: int) -> list[str]:
        """Return heat-source preset names available for a zone."""
        cfg = self.zone_configs.get(zone)
        if cfg is None or cfg.available_modes_mask == 0:
            return []
        mav = cfg.available_modes_mask
        return [
            name
            for name, mode_num in HEAT_TYPE_PRESETS.items()
            if mav & (1 << mode_num)
        ]

    def get_available_fan_modes(self, zone: int, mode_num: int) -> list[str]:
        """Return HA fan modes available for a zone/mode combo (from FA array)."""
        if mode_num in GAS_MODES:
            return ["auto"]
        cfg = self.zone_configs.get(zone)
        if cfg is None or mode_num < 0 or mode_num >= len(cfg.fan_array):
            return ["auto", "low", "high"]
        bitmask = cfg.fan_array[mode_num]
        if bitmask == 0:
            return []
        max_speed = bitmask & 0x0F
        allow_manual_auto = bool(bitmask & 0x40)
        allow_full_auto = bool(bitmask & 0x80)
        modes: list[str] = []
        if max_speed >= 2:
            modes += ["low", "high"]
        elif max_speed == 1:
            modes.append("low")
        if allow_full_auto or allow_manual_auto:
            modes.append("auto")
        return modes or ["auto"]

    # ──────────────────────────────────────────────────────────────────────────
    # Command API (called from HA event loop)
    # ──────────────────────────────────────────────────────────────────────────

    async def async_set_hvac_mode(self, zone: int, ha_mode: str) -> None:
        mode_num = HA_TO_DEVICE_DEFAULT.get(ha_mode, 0)
        if ha_mode == "heat":
            presets = self.get_available_presets(zone)
            if presets:
                first_mode = HEAT_TYPE_PRESETS.get(presets[0])
                if first_mode is not None:
                    mode_num = first_mode
        power = 0 if ha_mode == "off" else 1
        self._send_change({"zone": zone, "power": power, "mode": mode_num})
        self.suppress_status()

    async def async_set_preset_mode(self, zone: int, preset: str) -> None:
        mode_num = HEAT_TYPE_PRESETS.get(preset)
        if mode_num is None:
            _LOGGER.warning("Unknown preset: %s", preset)
            return
        self._send_change({"zone": zone, "power": 1, "mode": mode_num})
        self.suppress_status()

    async def async_set_temperature(
        self, zone: int, temp: float, mode_num: int
    ) -> None:
        ha_mode = DEVICE_TO_HA_MODE.get(mode_num, "off")
        field = {"cool": "cool_sp", "heat": "heat_sp", "dry": "dry_sp"}.get(ha_mode)
        if field is None:
            return
        self._send_change({"zone": zone, field: int(temp)})
        self.suppress_status()

    async def async_set_temperature_high(self, zone: int, temp: float) -> None:
        self._send_change({"zone": zone, "autoCool_sp": int(temp)})
        self.suppress_status()

    async def async_set_temperature_low(self, zone: int, temp: float) -> None:
        self._send_change({"zone": zone, "autoHeat_sp": int(temp)})
        self.suppress_status()

    async def async_set_fan_mode(self, zone: int, ha_fan: str, mode_num: int) -> None:
        fan_value = HA_FAN_TO_VALUE.get(ha_fan, 128)
        ha_mode = DEVICE_TO_HA_MODE.get(mode_num, "off")
        field_map = {
            "fan_only": FAN_FIELD_FAN_ONLY,
            "cool": FAN_FIELD_COOL,
            "heat": _fan_field_for_mode(mode_num),
            "auto": FAN_FIELD_AUTO,
        }
        field = field_map.get(ha_mode)
        if field is None:
            return
        self._send_change({"zone": zone, field: fan_value})
        self.suppress_status()

    async def async_reboot(self) -> None:
        self._send_change({"zone": 0, "reset": "OK"})
        self.suppress_status(10.0)

    def schedule_debounce(
        self,
        key: str,
        coro_factory: Callable[[], Any],
        delay: float = DEBOUNCE_DELAY_S,
    ) -> None:
        """Cancel any pending debounce for key and schedule a new one."""
        old = self._debounce_tasks.pop(key, None)
        if old and not old.done():
            old.cancel()

        async def _run() -> None:
            await asyncio.sleep(delay)
            try:
                await coro_factory()
            except Exception as exc:
                _LOGGER.debug("Debounce task error: %s", exc)

        self._debounce_tasks[key] = self.entry.async_create_background_task(
            self.hass, _run(), f"easytouch_wifi_debounce_{key}"
        )

    def _send_change(self, changes: dict) -> None:
        """Publish a Change command immediately (thread-safe)."""
        payload = json.dumps({"Type": "Change", "Changes": changes}, separators=(",", ":"))
        _LOGGER.info("Publishing command: %.120s", payload)
        self._publish(payload)

    def _publish(self, payload: str) -> None:
        """Publish a JSON payload to the device topic (thread-safe)."""
        if self._mqtt is not None and self._connected:
            self._mqtt.publish(self._topic, payload, qos=0)
        else:
            _LOGGER.debug("Cannot publish — not connected")

    # ──────────────────────────────────────────────────────────────────────────
    # Connection lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Set up MQTT client and begin polling. Called once at entry setup."""
        self._loop = self.hass.loop

        try:
            await self.hass.async_add_executor_job(self._setup_mqtt_client)
        except Exception as exc:
            _LOGGER.error("Failed to build SSL context: %s", exc)
            return

        try:
            await self.hass.async_add_executor_job(self._connect_sync)
        except Exception as exc:
            _LOGGER.error("Initial MQTT connect failed: %s", exc)
            self._schedule_reconnect(RECONNECT_BACKOFF_BASE_S)
            return

        # Start the poll loop (runs forever until async_disconnect)
        self._poll_task = self.entry.async_create_background_task(
            self.hass, self._poll_loop(), "easytouch_wifi_poll"
        )

    async def async_disconnect(self) -> None:
        """Clean up MQTT client and stop the poll loop."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None

        for task in self._debounce_tasks.values():
            if not task.done():
                task.cancel()
        self._debounce_tasks.clear()

        if self._mqtt is not None:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
            self._mqtt = None
        self._connected = False

    def _setup_mqtt_client(self) -> None:
        """Create and configure the paho MQTT client."""
        import paho.mqtt.client as mqtt

        client_id = f"ha_easytouch_{self._serial}"
        self._mqtt = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        self._mqtt.on_connect = self._on_connect_cb
        self._mqtt.on_message = self._on_message_cb
        self._mqtt.on_disconnect = self._on_disconnect_cb
        self._mqtt.reconnect_delay_set(
            min_delay=RECONNECT_BACKOFF_BASE_S,
            max_delay=RECONNECT_BACKOFF_CAP_S,
        )

        ssl_ctx = _build_ssl_context(self._client_cert, self._client_key)
        self._mqtt.tls_set_context(ssl_ctx)

    def _connect_sync(self) -> None:
        """Blocking MQTT connect + start network thread. Runs in executor."""
        _LOGGER.debug("Connecting to %s:%d", self._endpoint, MQTT_PORT)
        self._mqtt.connect(self._endpoint, MQTT_PORT, keepalive=60)
        self._mqtt.loop_start()

    # ──────────────────────────────────────────────────────────────────────────
    # paho callbacks (run in paho network thread — bridge to HA loop)
    # ──────────────────────────────────────────────────────────────────────────

    def _on_connect_cb(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        if reason_code.is_failure:
            _LOGGER.error(
                "EasyTouch %s MQTT connect failed: %s", self._serial, reason_code
            )
            return

        _LOGGER.info("EasyTouch %s connected to MQTT broker", self._serial)
        client.subscribe(self._topic)
        client.subscribe(self._topic + "/#")
        _LOGGER.debug("Subscribed to %s and %s/#", self._topic, self._topic)

        # Request zone configs (only on first connect; skip if already done)
        if not self._config_done:
            for zone in range(4):
                payload = json.dumps({"Type": "Get Config", "Zone": zone})
                client.publish(self._topic, payload, qos=0)

        # Signal HA event loop
        self._loop.call_soon_threadsafe(self._on_connected_ha)

    def _on_message_cb(self, client, userdata, msg) -> None:
        try:
            payload = msg.payload.decode("utf-8")
            obj = json.loads(payload)
        except Exception as exc:
            _LOGGER.warning("Failed to parse MQTT message: %s", exc)
            return
        self._loop.call_soon_threadsafe(self._handle_json, msg.topic, obj)

    def _on_disconnect_cb(self, client, userdata, disconnect_flags, reason_code, properties=None) -> None:
        _LOGGER.debug(
            "EasyTouch %s MQTT disconnected: %s", self._serial, reason_code
        )
        self._loop.call_soon_threadsafe(self._on_disconnected_ha)

    # ──────────────────────────────────────────────────────────────────────────
    # HA-loop callbacks (safe to access HA state)
    # ──────────────────────────────────────────────────────────────────────────

    @callback
    def _on_connected_ha(self) -> None:
        self._connected = True
        self._consecutive_failures = 0
        self._connected_event.set()
        # If we already had state, restore it (entities were unavailable during disconnect)
        if self.thermostat_state is not None:
            self.async_set_updated_data(self.thermostat_state)

    @callback
    def _on_disconnected_ha(self) -> None:
        self._connected = False
        self._connected_event.clear()
        # Mark entities unavailable
        self.async_set_updated_data(None)

    # ──────────────────────────────────────────────────────────────────────────
    # Poll loop
    # ──────────────────────────────────────────────────────────────────────────

    def _build_status_request(self, include_location: bool = False) -> dict:
        """Build a Get Status payload.

        TM (Unix timestamp) is always included. LAT/LON/DST are included only
        when include_location=True — on first connect and once per hour — so
        the device gets fresh weather coordinates without redundant data every poll.
        Format matches Android app (sendStatusRequest): LAT/LON as 5-decimal strings.
        """
        now_utc = datetime.now(ZoneInfo("UTC"))
        msg: dict = {"Type": "Get Status", "Zone": 0, "TM": int(now_utc.timestamp())}
        if include_location:
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude
            if lat is not None and lon is not None:
                tz = ZoneInfo(self.hass.config.time_zone)
                dst = datetime.now(tz).dst()
                dst_minutes = int(dst.total_seconds() / 60) if dst else 0
                msg["LAT"] = f"{lat:.5f}"
                msg["LON"] = f"{lon:.5f}"
                msg["DST"] = dst_minutes
        return msg

    async def _poll_loop(self) -> None:
        """Send periodic Get Status requests. Runs for the life of the connection."""
        # Wait for initial MQTT connection (with timeout)
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "EasyTouch %s: timed out waiting for MQTT connection", self._serial
            )
            return

        # Give config responses a moment to arrive before first status poll
        await asyncio.sleep(CONFIG_REQUEST_DELAY_S * 4)

        # Send location on first poll, then every hour (3600s / 10s interval = 360 polls).
        location_interval = max(1, round(3600 / MQTT_POLL_INTERVAL_S))
        poll_count = 0

        while True:
            try:
                await asyncio.sleep(MQTT_POLL_INTERVAL_S)
            except asyncio.CancelledError:
                return

            if self._connected:
                include_loc = (poll_count % location_interval) == 0
                self._publish(json.dumps(self._build_status_request(include_loc)))
                poll_count += 1

    # ──────────────────────────────────────────────────────────────────────────
    # Reconnect (for initial connect failure — paho handles subsequent reconnects)
    # ──────────────────────────────────────────────────────────────────────────

    def _schedule_reconnect(self, delay: float) -> None:
        if self._consecutive_failures >= RECONNECT_MAX_FAILURES:
            _LOGGER.error(
                "EasyTouch %s: giving up after %d failures. Reload to retry.",
                self._serial, self._consecutive_failures,
            )
            return
        self.entry.async_create_background_task(
            self.hass, self._reconnect_after(delay), "easytouch_wifi_reconnect"
        )

    async def _reconnect_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._consecutive_failures += 1
        try:
            if self._mqtt is None:
                self._setup_mqtt_client()
            await self.hass.async_add_executor_job(self._connect_sync)
            if self._poll_task is None or self._poll_task.done():
                self._poll_task = self.entry.async_create_background_task(
                    self.hass, self._poll_loop(), "easytouch_wifi_poll"
                )
        except Exception as exc:
            backoff = min(
                RECONNECT_BACKOFF_BASE_S * (2 ** min(self._consecutive_failures, 10)),
                RECONNECT_BACKOFF_CAP_S,
            )
            _LOGGER.warning("Reconnect failed: %s — retry in %.0fs", exc, backoff)
            self._schedule_reconnect(backoff)

    # ──────────────────────────────────────────────────────────────────────────
    # Message handling (HA event loop)
    # ──────────────────────────────────────────────────────────────────────────

    @callback
    def _handle_json(self, topic: str, obj: dict) -> None:
        rtype = obj.get("Type", "")
        rt = obj.get("RT", "")

        if (rtype == "Response" and rt == "Config") or rtype == "Config":
            self._parse_config(obj)
        elif (rtype == "Response" and rt == "Status") or rtype == "Status":
            self._parse_status(obj)
        elif rt == "OK":
            _LOGGER.info("Device acknowledged command")
        elif "Change" in rtype:
            _LOGGER.debug("Change message topic=%s payload=%s", topic, obj)
        elif rtype in ("Get Status", "Get Config", "Get Schedule", "ExtraData"):
            pass
        else:
            _LOGGER.debug("Unhandled message topic=%s Type=%r RT=%r payload=%s", topic, rtype, rt, obj)

    # ──────────────────────────────────────────────────────────────────────────
    # Config parsing
    # ──────────────────────────────────────────────────────────────────────────

    @callback
    def _parse_config(self, obj: dict) -> None:
        # Config response may wrap the zone data in a nested "CFG" object,
        # or the CFG field may be a JSON string — handle both.
        raw_cfg = obj.get("CFG")
        if raw_cfg is None:
            _LOGGER.debug("No CFG in config response")
            return

        cfg = raw_cfg
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except json.JSONDecodeError:
                _LOGGER.warning("Could not parse CFG string: %.80s", raw_cfg)
                return

        # Single-zone response: CFG dict has Zone/MAV/FA/SPL directly
        if "Zone" in cfg:
            self._store_zone_config(cfg)
        else:
            # Multi-zone response: CFG has keys "zone0", "zone1", …
            for key, zone_cfg in cfg.items():
                if key.startswith("zone") and isinstance(zone_cfg, dict):
                    self._store_zone_config(zone_cfg)

        # Mark config done once we've seen at least one valid zone
        active = [z for z, c in self.zone_configs.items() if c.available_modes_mask != 0]
        if active and not self._config_done:
            self._config_done = True
            _LOGGER.info(
                "EasyTouch %s config done. Active zones: %s", self._serial, active
            )

    def _store_zone_config(self, cfg: dict) -> None:
        zone = int(cfg.get("Zone", 0))
        mav = int(cfg.get("MAV", 0))
        fa_raw = cfg.get("FA", [])
        fa = list(fa_raw[:16]) + [0] * max(0, 16 - len(fa_raw))

        spl = cfg.get("SPL", [])
        min_cool = spl[0] if len(spl) > 0 else DEFAULT_MIN_TEMP
        max_cool = spl[1] if len(spl) > 1 else DEFAULT_MAX_TEMP
        min_heat = spl[2] if len(spl) > 2 else DEFAULT_MIN_TEMP
        max_heat = spl[3] if len(spl) > 3 else DEFAULT_MAX_TEMP

        self.zone_configs[zone] = ZoneConfig(
            zone=zone,
            available_modes_mask=mav,
            fan_array=fa,
            min_cool_sp=min_cool,
            max_cool_sp=max_cool,
            min_heat_sp=min_heat,
            max_heat_sp=max_heat,
        )
        _LOGGER.info(
            "Zone %d config: MAV=0x%X, cool=%d-%d°F, heat=%d-%d°F",
            zone, mav, min_cool, max_cool, min_heat, max_heat,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Status parsing
    # ──────────────────────────────────────────────────────────────────────────

    @callback
    def _parse_status(self, obj: dict) -> None:
        self._last_data_time = time.monotonic()
        self._update_device_info(obj)

        z_sts = obj.get("Z_sts")
        if not z_sts:
            _LOGGER.warning("No Z_sts in status response")
            return

        prm = obj.get("PRM", [])
        system_power = bool(prm[1] & PRM_FLAG_SYSTEM_POWER) if len(prm) > 1 else True

        zones: dict[int, ZoneState] = {}
        for key, arr in z_sts.items():
            try:
                zone_num = int(key)
            except (ValueError, TypeError):
                continue
            if not isinstance(arr, list) or len(arr) < 12:
                _LOGGER.warning("Invalid Z_sts data for zone %s", key)
                continue

            def _i(idx: int) -> int:
                return int(arr[idx]) if idx < len(arr) else 0

            flags = _i(IDX_STATUS_FLAGS)
            zones[zone_num] = ZoneState(
                zone=zone_num,
                ambient_temp=_i(IDX_AMBIENT_TEMP),
                cool_sp=_i(IDX_COOL_SP),
                heat_sp=_i(IDX_HEAT_SP),
                auto_cool_sp=_i(IDX_AUTO_COOL_SP),
                auto_heat_sp=_i(IDX_AUTO_HEAT_SP),
                dry_sp=_i(IDX_DRY_SP),
                mode_num=_i(IDX_MODE) & 0x0F,  # mask upper nibble per protocol
                fan_only_speed=_i(IDX_FAN_ONLY_SPEED),
                cool_fan_speed=_i(IDX_COOL_FAN_SPEED),
                electric_fan_speed=_i(IDX_ELECTRIC_FAN_SPEED),
                auto_fan_speed=_i(IDX_AUTO_FAN_SPEED),
                gas_fan_speed=_i(IDX_GAS_FAN_SPEED),
                fault=_i(IDX_FAULT),
                cycle_active=bool(flags & FLAG_CYCLE_ACTIVE),
                is_cooling=bool(flags & FLAG_IS_COOLING),
                is_heating=bool(flags & FLAG_IS_HEATING),
            )

        available_zones = sorted(zones.keys())

        if self.is_status_suppressed():
            _LOGGER.debug("Status suppressed (command in progress)")
            return

        self.thermostat_state = ThermostatState(
            available_zones=available_zones,
            zones=zones,
            system_power=system_power,
            serial_number=self.serial_number,
            firmware_version=self.firmware_version,
            device_type=self.device_type,
            config_index=self.config_index,
            model_number=self.device_model,
        )
        self.async_set_updated_data(self.thermostat_state)

    @callback
    def _update_device_info(self, obj: dict) -> None:
        sn = obj.get("SN")
        if sn and not self.serial_number:
            self.serial_number = str(sn)
            if len(self.serial_number) >= 3 and not self.device_model:
                self.device_model = self.serial_number[:3]
        rev = obj.get("REV")
        if rev and not self.firmware_version:
            self.firmware_version = str(rev)
        tt = obj.get("TT")
        if tt and not self.device_type:
            self.device_type = str(tt)
        ci = obj.get("CI")
        if ci is not None and not self.config_index:
            self.config_index = str(ci)

    # ──────────────────────────────────────────────────────────────────────────
    # DataUpdateCoordinator hook (push-based; no-op)
    # ──────────────────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> ThermostatState | None:
        return self.thermostat_state
