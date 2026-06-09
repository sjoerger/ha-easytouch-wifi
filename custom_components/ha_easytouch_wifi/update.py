"""Firmware update entity for EasyTouch Wi-Fi thermostat."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    APP_CLIENT_ID,
    APP_CLIENT_SECRET,
    CONF_SERIAL,
    DOMAIN,
    IDENTITY_POOL_ID,
    REGION,
    USER_POOL_ID,
)
from .coordinator import EasyTouchMQTTCoordinator

_LOGGER = logging.getLogger(__name__)

UPDATE_CHECK_INTERVAL = timedelta(hours=24)
DYNAMO_TABLE = "Updates"


def _fetch_firmware_record_sync(username: str, password: str, model: str) -> dict | None:
    """Authenticate with Cognito and fetch the latest firmware record from DynamoDB.

    Runs in an executor thread (blocking I/O).
    Returns a plain dict of field→value strings, or None if no record found.
    """
    try:
        from pycognito import Cognito
    except ImportError as exc:
        raise RuntimeError("pycognito is not installed") from exc

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is not installed") from exc

    u = Cognito(
        user_pool_id=USER_POOL_ID,
        client_id=APP_CLIENT_ID,
        client_secret=APP_CLIENT_SECRET,
        username=username,
    )
    u.authenticate(password=password)

    login_key = f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
    identity_client = boto3.client("cognito-identity", region_name=REGION)

    id_resp = identity_client.get_id(
        IdentityPoolId=IDENTITY_POOL_ID,
        Logins={login_key: u.id_token},
    )
    creds_resp = identity_client.get_credentials_for_identity(
        IdentityId=id_resp["IdentityId"],
        Logins={login_key: u.id_token},
    )
    creds = creds_resp["Credentials"]

    dynamo = boto3.client(
        "dynamodb",
        region_name=REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretKey"],
        aws_session_token=creds["SessionToken"],
    )

    resp = dynamo.get_item(
        TableName=DYNAMO_TABLE,
        Key={"Device_type": {"S": model}},
    )
    item = resp.get("Item")
    if not item:
        return None

    return {k: next(iter(v.values())) for k, v in item.items()}


def _is_newer(available: str, installed: str) -> bool:
    """Return True if available version is strictly newer than installed.

    Compares 4-component semantic versions (MAJOR.MINOR.PATCH.BUILD).
    Build number 99 is treated as a development build and always considered
    up-to-date (matching Android app behaviour).
    """
    try:
        def _parts(v: str) -> list[int]:
            parts = [int(x) for x in v.split(".")]
            while len(parts) < 4:
                parts.append(0)
            return parts

        avail = _parts(available)
        inst = _parts(installed)

        if 99 in inst:
            return False

        return avail > inst
    except (ValueError, AttributeError):
        return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EasyTouchMQTTCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EasyTouchFirmwareUpdate(coordinator, entry)])


class EasyTouchFirmwareUpdate(CoordinatorEntity[EasyTouchMQTTCoordinator], UpdateEntity):
    """Firmware update entity — checks DynamoDB daily, installs via MQTT UPDATE command."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware_update"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(
        self,
        coordinator: EasyTouchMQTTCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        serial = entry.data[CONF_SERIAL]
        self._serial = serial
        self._model = serial[:3]
        self._username = entry.data.get(CONF_USERNAME, "")
        self._password = entry.data.get(CONF_PASSWORD, "")
        self._latest_version: str | None = None
        self._firmware_record: dict | None = None
        self._attr_unique_id = f"easytouch_wifi_{serial}_firmware_update"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"EasyTouch {serial}",
            manufacturer="Micro-Air",
            model="EasyTouch RV Wi-Fi",
            serial_number=serial,
        )

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.firmware_version

    @property
    def latest_version(self) -> str | None:
        return self._latest_version

    @property
    def release_url(self) -> str | None:
        if self._firmware_record:
            return self._firmware_record.get("WebURL")
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        if not self._password:
            _LOGGER.warning(
                "EasyTouch %s: no password stored — firmware update checks unavailable. "
                "Remove and re-add the integration to enable this feature.",
                self._serial,
            )
            return

        # Check on startup then every 24 hours
        self._entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._scheduled_check, UPDATE_CHECK_INTERVAL
            )
        )
        self._entry.async_create_background_task(
            self.hass, self._check_for_updates(), "easytouch_wifi_update_check"
        )

    async def _scheduled_check(self, _now=None) -> None:
        await self._check_for_updates()

    async def _check_for_updates(self) -> None:
        try:
            record = await self.hass.async_add_executor_job(
                _fetch_firmware_record_sync,
                self._username,
                self._password,
                self._model,
            )
        except Exception as exc:
            _LOGGER.warning("EasyTouch %s: firmware update check failed: %s", self._serial, exc)
            return

        if record is None:
            _LOGGER.warning("EasyTouch %s: no firmware record found for model %s", self._serial, self._model)
            return

        self._firmware_record = record
        self._latest_version = record.get("Revision")
        _LOGGER.info(
            "EasyTouch %s: installed=%s latest=%s update_available=%s",
            self._serial,
            self.installed_version,
            self._latest_version,
            self.update_available,
        )
        self.async_write_ha_state()

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Send the MQTT UPDATE command — device downloads firmware and reboots."""
        if not self._firmware_record:
            _LOGGER.error("EasyTouch %s: no firmware record available to install", self._serial)
            return

        rec = self._firmware_record
        self.coordinator.publish_json({
            "Type": "UPDATE",
            "Action": rec.get("Action", "FW"),
            "Device Type": rec.get("Device_type", self._model),
            "Serial": self._serial,
            "Revision": rec.get("Revision", ""),
            "RevCheckMode": rec.get("RevCheckMode", "Off"),
            "SerialCheckMode": "Match",
            "Web Server": rec.get("WebServer", ""),
            "Web URL": rec.get("WebURL", ""),
            "Port": rec.get("Port", "443"),
        })
        _LOGGER.info(
            "EasyTouch %s: firmware update command sent — device will reboot after flashing",
            self._serial,
        )
