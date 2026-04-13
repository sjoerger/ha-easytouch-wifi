"""Button entities for EasyTouch Wi-Fi thermostat."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SERIAL, DOMAIN
from .coordinator import EasyTouchMQTTCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EasyTouchMQTTCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EasyTouchRebootButton(coordinator, entry)])


class EasyTouchRebootButton(ButtonEntity):
    """Button that sends a reset command to the thermostat firmware."""

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
