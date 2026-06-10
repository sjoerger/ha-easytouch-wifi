"""Button entities for EasyTouch Wi-Fi thermostat."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_BLE_PASSWORD, CONF_SERIAL, DOMAIN
from .coordinator import EasyTouchMQTTCoordinator

_LOGGER = logging.getLogger(__name__)

# BLE GATT UUIDs (confirmed from ha-easytouch + Android BluetoothLeService.java)
_BLE_PWD_UUID   = "0000dd01-0000-1000-8000-00805f9b34fb"   # password auth (written once on connect)
_BLE_CMD_UUID   = "0000ee01-0000-1000-8000-00805f9b34fb"   # write command
_BLE_RSP_UUID   = "0000ff01-0000-1000-8000-00805f9b34fb"   # read response
_BLE_REBOOT_CMD = b'{"Type":"Change","Changes":{"zone":0,"reset":" OK"}}'  # full JSON wrapper required
_BLE_AUTH_DELAY      = 0.20   # seconds after connect before/after auth (matches ha-easytouch)
_BLE_POST_WRITE_DELAY = 0.10  # seconds to wait before reading response

# The thermostat advertises as "EasyTouch <serial>" — use this for direct identification
# rather than reading the Device Information Service serial characteristic (not present).
_BLE_NAME_PREFIX = "EasyTouch "


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EasyTouchMQTTCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EasyTouchRebootButton(coordinator, entry),
        EasyTouchBLERebootButton(hass, entry),
    ])


class EasyTouchRebootButton(ButtonEntity):
    """Button that sends a reset command over MQTT/cloud."""

    _attr_has_entity_name = True
    _attr_translation_key = "reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self,
        coordinator: EasyTouchMQTTCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self._coordinator = coordinator
        serial = entry.data[CONF_SERIAL]
        self._attr_unique_id = f"easytouch_wifi_{serial}_reboot"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"EasyTouch {serial}",
            manufacturer="Micro-Air",
            model="EasyTouch RV Wi-Fi",
            serial_number=serial,
        )

    async def async_press(self) -> None:
        await self._coordinator.async_reboot()


class EasyTouchBLERebootButton(ButtonEntity):
    """Button that sends a reboot command via Bluetooth LE.

    Useful as a fallback when the thermostat has lost its cloud connection.
    The HA host must have a Bluetooth adapter within BLE range of the thermostat.
    Scans HA's BLE discovery cache, connects to any EasyTouch device, verifies the
    serial number, then writes the reboot JSON to the command characteristic.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "bluetooth_reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:bluetooth-transfer"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._serial = entry.data[CONF_SERIAL]
        self._ble_password = entry.data.get(CONF_BLE_PASSWORD, "")
        self._attr_unique_id = f"easytouch_wifi_{self._serial}_bluetooth_reboot"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EasyTouch {self._serial}",
            manufacturer="Micro-Air",
            model="EasyTouch RV Wi-Fi",
            serial_number=self._serial,
        )

    async def async_press(self) -> None:
        """Find thermostat via BLE advertisement name, authenticate, send reboot command."""
        import asyncio
        try:
            from homeassistant.components.bluetooth import async_discovered_service_info
            from bleak import BleakClient, BleakError, BleakScanner
            from bleak_retry_connector import establish_connection
        except ImportError:
            _LOGGER.error(
                "EasyTouch %s: Bluetooth support unavailable in this HA installation",
                self._serial,
            )
            return

        target_name = f"{_BLE_NAME_PREFIX}{self._serial}"

        # Fast path: check HA's passive scan cache first
        device = next(
            (
                info.device
                for info in async_discovered_service_info(self.hass, connectable=True)
                if info.name == target_name
            ),
            None,
        )

        # Slow path: active scan if not in cache
        if device is None:
            _LOGGER.debug(
                "EasyTouch %s: not in scan cache — running active BLE scan (5s)...",
                self._serial,
            )
            try:
                found = await BleakScanner.discover(timeout=5.0)
                device = next((d for d in found if d.name == target_name), None)
            except BleakError as exc:
                _LOGGER.error(
                    "EasyTouch %s: BLE scan failed — is Bluetooth hardware available? (%s)",
                    self._serial, exc,
                )
                return

        if device is None:
            _LOGGER.warning(
                "EasyTouch %s: BLE device %r not found — "
                "thermostat may be out of Bluetooth range",
                self._serial, target_name,
            )
            return

        _LOGGER.debug("EasyTouch %s: connecting to %s", self._serial, device.address)
        # BleakClientWithServiceCache caches service discovery between connections,
        # matching ha-easytouch's approach for reliable characteristic access.
        try:
            from bleak_retry_connector import BleakClientWithServiceCache
            client_cls = BleakClientWithServiceCache
        except ImportError:
            client_cls = BleakClient

        client = None
        try:
            client = await establish_connection(client_cls, device, target_name)

            # Read device info first — ha-easytouch does this before writing commands
            # (sequence: connect → 200ms → read info → 200ms → auth → command).
            await asyncio.sleep(_BLE_AUTH_DELAY)
            for info_uuid in (
                "00002a26-0000-1000-8000-00805f9b34fb",   # firmware revision
                "00002a24-0000-1000-8000-00805f9b34fb",   # model number
            ):
                try:
                    await client.read_gatt_char(info_uuid)
                    break
                except BleakError:
                    pass
            await asyncio.sleep(_BLE_AUTH_DELAY)

            # Authenticate: write password to DD01 before sending commands (only if set).
            if self._ble_password:
                try:
                    await client.write_gatt_char(
                        _BLE_PWD_UUID, self._ble_password.encode("utf-8"), response=True
                    )
                    _LOGGER.debug("EasyTouch %s: BLE password written", self._serial)
                except BleakError as exc:
                    _LOGGER.debug(
                        "EasyTouch %s: BLE password write failed (continuing): %s",
                        self._serial, exc,
                    )

            await client.write_gatt_char(_BLE_CMD_UUID, _BLE_REBOOT_CMD, response=True)

            # Write with response=True means the device acknowledged receipt at the GATT level.
            # Log success immediately — the thermostat may start rebooting before we can read back.
            _LOGGER.info(
                "EasyTouch %s: BLE reboot command acknowledged by device at %s",
                self._serial, device.address,
            )

            # Best-effort response read — may fail if device reboots immediately.
            await asyncio.sleep(_BLE_POST_WRITE_DELAY)
            try:
                rsp_raw = await client.read_gatt_char(_BLE_RSP_UUID)
                rsp = rsp_raw.decode("utf-8", errors="replace").strip()
                _LOGGER.debug("EasyTouch %s: BLE response: %s", self._serial, rsp)
            except BleakError:
                _LOGGER.debug(
                    "EasyTouch %s: no BLE response (device likely rebooting)", self._serial
                )

        except BleakError as exc:
            _LOGGER.warning(
                "EasyTouch %s: BLE reboot failed: %s", self._serial, exc,
            )
        finally:
            if client:
                await client.disconnect()
