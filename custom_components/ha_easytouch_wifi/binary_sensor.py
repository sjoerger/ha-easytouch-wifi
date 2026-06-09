"""Binary sensor entities for EasyTouch Wi-Fi thermostat (diagnostic)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import EasyTouchMQTTCoordinator


@dataclass(frozen=True, kw_only=True)
class EasyTouchBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: callable = lambda coordinator: False
    available_fn: callable = lambda coordinator: True


BINARY_SENSOR_DESCRIPTIONS: tuple[EasyTouchBinarySensorDescription, ...] = (
    EasyTouchBinarySensorDescription(
        key="cloud_connected",
        translation_key="cloud_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.connected,
    ),
    EasyTouchBinarySensorDescription(
        key="data_healthy",
        translation_key="data_healthy",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: not c.data_healthy,  # PROBLEM = True when data is stale
    ),
    EasyTouchBinarySensorDescription(
        key="device_wifi_connected",
        translation_key="device_wifi_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data is not None and c.data.device_wifi_connected,
        available_fn=lambda c: c.data_healthy,  # stale data = can't trust device-reported status
    ),
    EasyTouchBinarySensorDescription(
        key="device_aws_connected",
        translation_key="device_aws_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_registry_enabled_default=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.data is not None and c.data.device_aws_connected,
        available_fn=lambda c: c.data_healthy,  # stale data = can't trust device-reported status
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EasyTouchMQTTCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EasyTouchBinarySensor(coordinator, entry, desc)
        for desc in BINARY_SENSOR_DESCRIPTIONS
    )


class EasyTouchBinarySensor(
    CoordinatorEntity[EasyTouchMQTTCoordinator], BinarySensorEntity
):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EasyTouchMQTTCoordinator,
        entry: ConfigEntry,
        description: EasyTouchBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        serial = entry.data[CONF_SERIAL]
        self._attr_unique_id = f"easytouch_wifi_{serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"EasyTouch {serial}",
            manufacturer="Micro-Air",
            model="EasyTouch RV Wi-Fi",
            serial_number=serial,
        )
        self._description = description

    @property
    def available(self) -> bool:
        return self._description.available_fn(self.coordinator)

    @property
    def is_on(self) -> bool | None:
        return self._description.value_fn(self.coordinator)
