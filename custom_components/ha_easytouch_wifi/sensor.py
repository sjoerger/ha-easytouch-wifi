"""Diagnostic sensor entities for EasyTouch Wi-Fi thermostat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import EasyTouchMQTTCoordinator


@dataclass(frozen=True, kw_only=True)
class EasyTouchSensorDescription(SensorEntityDescription):
    value_fn: callable = lambda coordinator: None


SENSOR_DESCRIPTIONS: tuple[EasyTouchSensorDescription, ...] = (
    EasyTouchSensorDescription(
        key="serial_number",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        icon="mdi:identifier",
        value_fn=lambda c: c.serial_number,
    ),
    EasyTouchSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        icon="mdi:chip",
        value_fn=lambda c: c.firmware_version,
    ),
    EasyTouchSensorDescription(
        key="model_number",
        translation_key="model_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        icon="mdi:barcode",
        value_fn=lambda c: c.device_model,
    ),
    EasyTouchSensorDescription(
        key="device_type",
        translation_key="device_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
        icon="mdi:thermostat",
        value_fn=lambda c: c.device_type,
    ),
    EasyTouchSensorDescription(
        key="mqtt_endpoint",
        translation_key="mqtt_endpoint",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:cloud-outline",
        value_fn=lambda c: c._endpoint,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EasyTouchMQTTCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EasyTouchSensor(coordinator, entry, desc) for desc in SENSOR_DESCRIPTIONS
    )


class EasyTouchSensor(CoordinatorEntity[EasyTouchMQTTCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EasyTouchMQTTCoordinator,
        entry: ConfigEntry,
        description: EasyTouchSensorDescription,
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
    def native_value(self) -> Any:
        return self._description.value_fn(self.coordinator)
