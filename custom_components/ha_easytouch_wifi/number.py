"""Number entities for EasyTouch Wi-Fi thermostat alert thresholds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import EasyTouchMQTTCoordinator
from .models import ThermostatState

ALERT_LOW_MIN = 40
ALERT_LOW_MAX = 108   # must be at least 2 below alertUL max of 110
ALERT_HIGH_MIN = 42   # must be at least 2 above alertLL min of 40
ALERT_HIGH_MAX = 110


@dataclass(frozen=True, kw_only=True)
class EasyTouchNumberDescription(NumberEntityDescription):
    mqtt_field: str = ""
    value_fn: Any = None
    peer_fn: Any = None   # returns the peer threshold value for gap enforcement


ALERT_DESCRIPTIONS: tuple[EasyTouchNumberDescription, ...] = (
    EasyTouchNumberDescription(
        key="alert_low_temp",
        translation_key="alert_low_temp",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=ALERT_LOW_MIN,
        native_max_value=ALERT_LOW_MAX,
        native_step=1,
        mode=NumberMode.BOX,
        mqtt_field="alertLL",
        value_fn=lambda state: state.alert_low,
        peer_fn=lambda state: state.alert_high,
    ),
    EasyTouchNumberDescription(
        key="alert_high_temp",
        translation_key="alert_high_temp",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=ALERT_HIGH_MIN,
        native_max_value=ALERT_HIGH_MAX,
        native_step=1,
        mode=NumberMode.BOX,
        mqtt_field="alertUL",
        value_fn=lambda state: state.alert_high,
        peer_fn=lambda state: state.alert_low,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EasyTouchMQTTCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        EasyTouchAlertNumber(coordinator, entry, desc) for desc in ALERT_DESCRIPTIONS
    )


class EasyTouchAlertNumber(CoordinatorEntity[EasyTouchMQTTCoordinator], NumberEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EasyTouchMQTTCoordinator,
        entry: ConfigEntry,
        description: EasyTouchNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._description = description
        serial = entry.data[CONF_SERIAL]
        self._attr_unique_id = f"easytouch_wifi_{serial}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"EasyTouch {serial}",
            manufacturer="Micro-Air",
            model="EasyTouch RV Wi-Fi",
            serial_number=serial,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def native_value(self) -> float | None:
        state: ThermostatState | None = self.coordinator.data
        if state is None:
            return None
        return float(self._description.value_fn(state))

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        state: ThermostatState | None = self.coordinator.data
        if state is None:
            return

        new_val = int(value)
        peer = int(self._description.peer_fn(state))

        # Enforce 2°F minimum gap (matches Android app validation)
        if self._description.mqtt_field == "alertLL" and new_val > peer - 2:
            new_val = peer - 2
        elif self._description.mqtt_field == "alertUL" and new_val < peer + 2:
            new_val = peer + 2

        self.coordinator.send_change({self._description.mqtt_field: new_val})
