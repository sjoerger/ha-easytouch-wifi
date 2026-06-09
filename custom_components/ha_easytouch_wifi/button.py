"""Button entities for EasyTouch Wi-Fi thermostat."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SERIAL, DOMAIN
from .coordinator import EasyTouchMQTTCoordinator

_LOGGER = logging.getLogger(__name__)

# BLE GATT UUIDs (confirmed from ha-easytouch and Android BluetoothLeService.java)
_BLE_SERIAL_UUID = "00002a25-0000-1000-8000-00805f9b34fb"   # Device Information: Serial Number
_BLE_CMD_UUID    = "0000ee01-0000-1000-8000-00805f9b34fb"   # EasyTouch JSON command characteristic
_BLE_REBOOT_CMD  = b'{"zone":0,"reset":" OK"}'              # space before OK matches BLE protocol


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
        self._attr_unique_id = f"easytouch_wifi_{self._serial}_bluetooth_reboot"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EasyTouch {self._serial}",
            manufacturer="Micro-Air",
            model="EasyTouch RV Wi-Fi",
            serial_number=self._serial,
        )

    async def async_press(self) -> None:
        """Find thermostat via BLE, verify serial, send reboot command."""
        try:
            from homeassistant.components.bluetooth import async_discovered_service_info
            from bleak import BleakClient, BleakError
        except ImportError:
            _LOGGER.error(
                "EasyTouch %s: Bluetooth support unavailable in this HA installation",
                self._serial,
            )
            return

        candidates = [
            info
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.name and "EasyTouch" in info.name
        ]

        if not candidates:
            _LOGGER.warning(
                "EasyTouch %s: no EasyTouch BLE devices in scan cache — "
                "ensure the thermostat is within Bluetooth range",
                self._serial,
            )
            return

        _LOGGER.debug(
            "EasyTouch %s: %d BLE candidate(s) found: %s",
            self._serial,
            len(candidates),
            [i.address for i in candidates],
        )

        for info in candidates:
            try:
                async with BleakClient(info.device) as client:
                    serial_raw = await client.read_gatt_char(_BLE_SERIAL_UUID)
                    ble_serial = serial_raw.decode("utf-8").strip()

                    if ble_serial != self._serial:
                        _LOGGER.debug(
                            "BLE device %s has serial %s, need %s — skipping",
                            info.address, ble_serial, self._serial,
                        )
                        continue

                    await client.write_gatt_char(_BLE_CMD_UUID, _BLE_REBOOT_CMD, response=True)
                    _LOGGER.info(
                        "EasyTouch %s: BLE reboot command sent via %s",
                        self._serial, info.address,
                    )
                    return

            except BleakError as exc:
                _LOGGER.warning(
                    "EasyTouch %s: BLE connection to %s failed: %s",
                    self._serial, info.address, exc,
                )

        _LOGGER.warning(
            "EasyTouch %s: no matching BLE device found among %d candidate(s) — "
            "device may be out of Bluetooth range",
            self._serial, len(candidates),
        )
