"""Tests for Buffalo LinkStation config flows."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.buffalo_linkstation.api import (
    BuffaloAuthenticationError,
    BuffaloIdentity,
)
from custom_components.buffalo_linkstation.config_flow import ValidationResult
from custom_components.buffalo_linkstation.const import (
    CONF_DEVICE_ID,
    CONF_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

INPUT = {
    CONF_HOST: "nas.local",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_UPDATE_INTERVAL: 60,
    CONF_TIMEOUT: 10,
}
VALID = ValidationResult(
    BuffaloIdentity(
        hostname="office-nas",
        model="LS220D",
        firmware="1.86",
        serial_number="ABC123",
    ),
    "serial:abc123",
)


async def test_user_flow_success(hass):
    with patch(
        "custom_components.buffalo_linkstation.config_flow._async_validate",
        return_value=VALID,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=INPUT,
        )

    assert result["type"] is config_entries.ConfigFlowResultType.CREATE_ENTRY
    assert result["title"] == "office-nas"
    assert result["data"][CONF_DEVICE_ID] == "serial:abc123"
    assert result["result"].unique_id == "serial:abc123"


async def test_invalid_credentials(hass):
    with patch(
        "custom_components.buffalo_linkstation.config_flow._async_validate",
        side_effect=BuffaloAuthenticationError("bad"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=INPUT,
        )
    assert result["type"] is config_entries.ConfigFlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_device_is_rejected(hass):
    with patch(
        "custom_components.buffalo_linkstation.config_flow._async_validate",
        return_value=VALID,
    ):
        first = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=INPUT,
        )
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={**INPUT, CONF_HOST: "192.0.2.5"},
        )
    assert first["type"] is config_entries.ConfigFlowResultType.CREATE_ENTRY
    assert second["type"] is config_entries.ConfigFlowResultType.ABORT
    assert second["reason"] == "already_configured"


async def test_multiple_devices_create_independent_entries(hass):
    second = ValidationResult(
        BuffaloIdentity(hostname="backup", model="LS420D", serial_number="XYZ"),
        "serial:xyz",
    )
    with patch(
        "custom_components.buffalo_linkstation.config_flow._async_validate",
        side_effect=[VALID, second],
    ):
        one = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=INPUT,
        )
        two = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={**INPUT, CONF_HOST: "backup.local"},
        )
    assert one["result"].entry_id != two["result"].entry_id
    assert {entry.unique_id for entry in hass.config_entries.async_entries(DOMAIN)} == {
        "serial:abc123",
        "serial:xyz",
    }
