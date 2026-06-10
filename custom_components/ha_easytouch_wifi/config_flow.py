"""Config flow for the EasyTouch Wi-Fi integration.

Flow:
  1. user      — enter AWS Cognito username + password
  2. provision — background task: SRP auth → IoT certificate provisioning
  3. serial    — enter device serial number (e.g. 352016109)
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    AMAZON_ROOT_CA_1,
    APP_CLIENT_ID,
    APP_CLIENT_SECRET,
    CONF_BLE_PASSWORD,
    CONF_CA_PEM,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_MQTT_ENDPOINT,
    CONF_SERIAL,
    DOMAIN,
    IDENTITY_POOL_ID,
    IOT_ENDPOINT_FALLBACK,
    IOT_POLICY_NAME,
    REGION,
    USER_POOL_ID,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_SERIAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERIAL): str,
        vol.Optional(CONF_BLE_PASSWORD, default=""): str,
    }
)


def _provision_sync(username: str, password: str) -> dict:
    """Authenticate with Cognito, provision an AWS IoT certificate.

    Runs in an executor thread (blocking I/O).

    Returns a dict with keys: ca_pem, client_cert, client_key, endpoint.
    Raises on any auth or provisioning failure.
    """
    # Import heavyweight libraries here so HA can load the integration
    # before they are fully installed.
    try:
        from pycognito import Cognito
    except ImportError as exc:
        raise RuntimeError(
            "pycognito is not installed. Restart Home Assistant to finish setup."
        ) from exc

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is not installed. Restart Home Assistant to finish setup."
        ) from exc

    # ── Step 1: SRP authentication with Cognito User Pool ────────────────────
    _LOGGER.debug("Authenticating with Cognito as %s", username)
    u = Cognito(
        user_pool_id=USER_POOL_ID,
        client_id=APP_CLIENT_ID,
        client_secret=APP_CLIENT_SECRET,
        username=username,
    )
    u.authenticate(password=password)
    id_token = u.id_token

    # ── Step 2: Exchange ID token for temporary AWS credentials ───────────────
    _LOGGER.debug("Exchanging Cognito token for AWS credentials")
    identity_client = boto3.client("cognito-identity", region_name=REGION)

    identity_resp = identity_client.get_id(
        IdentityPoolId=IDENTITY_POOL_ID,
        Logins={
            f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": id_token
        },
    )
    identity_id = identity_resp["IdentityId"]

    creds_resp = identity_client.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={
            f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}": id_token
        },
    )
    aws_creds = creds_resp["Credentials"]

    # ── Step 3: Provision IoT certificate ────────────────────────────────────
    _LOGGER.debug("Provisioning AWS IoT certificate")
    iot = boto3.client(
        "iot",
        region_name=REGION,
        aws_access_key_id=aws_creds["AccessKeyId"],
        aws_secret_access_key=aws_creds["SecretKey"],
        aws_session_token=aws_creds["SessionToken"],
    )

    # Get the ATS endpoint
    try:
        endpoint_resp = iot.describe_endpoint(endpointType="iot:Data-ATS")
        endpoint = endpoint_resp["endpointAddress"]
    except Exception:
        _LOGGER.warning("Could not fetch IoT endpoint; using fallback")
        endpoint = IOT_ENDPOINT_FALLBACK

    # Create a new certificate+key pair
    cert_resp = iot.create_keys_and_certificate(setAsActive=True)
    client_cert = cert_resp["certificatePem"]
    client_key = cert_resp["keyPair"]["PrivateKey"]
    cert_arn = cert_resp["certificateArn"]

    # Attach the IoT policy so this cert can connect and publish/subscribe
    iot.attach_policy(policyName=IOT_POLICY_NAME, target=cert_arn)
    _LOGGER.info("Provisioned IoT certificate: %s", cert_resp["certificateId"])

    return {
        "ca_pem": AMAZON_ROOT_CA_1,
        "client_cert": client_cert,
        "client_key": client_key,
        "endpoint": endpoint,
    }


class EasyTouchWiFiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow for EasyTouch Wi-Fi."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._provision_task = None
        self._provision_result: dict | None = None
        self._provision_error: str | None = None

    # ── Step 1: Credentials ───────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect AWS Cognito credentials."""
        errors: dict[str, str] = {}

        if self._provision_error:
            errors["base"] = "provision_failed"
            self._provision_error = None

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password_temp = user_input[CONF_PASSWORD]
            self._provision_task = None  # reset any previous attempt
            return await self.async_step_provision()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    # ── Step 2: Provisioning (background task) ────────────────────────────────

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run AWS provisioning and wait for it to complete."""
        if self._provision_task is None:
            self._provision_task = self.hass.async_add_executor_job(
                _provision_sync, self._username, self._password_temp
            )

        if not self._provision_task.done():
            return self.async_show_progress(
                step_id="provision",
                progress_action="provisioning",
                progress_task=self._provision_task,
            )

        # Task complete — check result
        try:
            self._provision_result = self._provision_task.result()
        except Exception as exc:
            _LOGGER.error("Provisioning failed: %s", exc)
            self._provision_error = str(exc)
            self._provision_task = None
            return self.async_show_progress_done(next_step_id="user")

        return self.async_show_progress_done(next_step_id="serial")

    # ── Step 3: Device serial number ──────────────────────────────────────────

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the device serial number and create the config entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial = user_input[CONF_SERIAL].strip()
            if not serial:
                errors[CONF_SERIAL] = "serial_empty"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                result = self._provision_result
                return self.async_create_entry(
                    title=f"EasyTouch {serial}",
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password_temp,
                        CONF_SERIAL: serial,
                        CONF_BLE_PASSWORD: user_input.get(CONF_BLE_PASSWORD, ""),
                        CONF_CA_PEM: result["ca_pem"],
                        CONF_CLIENT_CERT: result["client_cert"],
                        CONF_CLIENT_KEY: result["client_key"],
                        CONF_MQTT_ENDPOINT: result["endpoint"],
                    },
                )

        return self.async_show_form(
            step_id="serial",
            data_schema=STEP_SERIAL_SCHEMA,
            errors=errors,
            description_placeholders={"username": self._username or ""},
        )
