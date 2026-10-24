"""Tests for Buffalo LinkStation config flows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation.api import (
    BuffaloAuthenticationError,
    BuffaloIdentity,
    BuffaloInvalidResponseError,
)
from custom_components.buffalo_linkstation.config_flow import (
    ValidationResult,
    _async_validate,
)
from custom_components.buffalo_linkstation.const import (
    CONF_DEVICE_ID,
    CONF_ENABLE_REBOOT,
    CONF_ENABLE_SHUTDOWN,
    CONF_PROTOCOL,
    CONF_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    PROTOCOL_JSON_RPC,
    PROTOCOL_LEGACY_CGI,
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


@pytest.fixture(autouse=True)
def prevent_entry_setup_network_calls():
    """Keep config-flow tests isolated from network-backed entry setup."""
    with patch(
        "custom_components.buffalo_linkstation.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        yield


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

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "office-nas"
    assert result["data"][CONF_DEVICE_ID] == "serial:abc123"
    assert result["data"][CONF_PROTOCOL] == PROTOCOL_JSON_RPC
    assert result["result"].unique_id == "serial:abc123"


async def test_safe_protocol_detection_falls_back_to_legacy_cgi(hass):
    modern = MagicMock()
    modern.prepare_update = AsyncMock(
        side_effect=BuffaloInvalidResponseError("not JSON-RPC")
    )
    modern.shutdown = AsyncMock()
    modern.reboot = AsyncMock()
    legacy = MagicMock()
    legacy.prepare_update = AsyncMock()
    legacy.get_features = AsyncMock(return_value={"PRODUCT_NAME": "LS-WVL(KOUGYOKU)"})
    legacy.get_system_info = AsyncMock(
        return_value={"product_name": "LS-WVL(KOUGYOKU)"}
    )
    legacy.shutdown = AsyncMock()
    legacy.reboot = AsyncMock()

    with (
        patch(
            "custom_components.buffalo_linkstation.config_flow.BuffaloLinkStationApi",
            return_value=modern,
        ),
        patch(
            "custom_components.buffalo_linkstation.config_flow.LegacyLinkStationApi",
            return_value=legacy,
        ),
    ):
        result = await _async_validate(hass, INPUT)

    assert result.protocol == PROTOCOL_LEGACY_CGI
    assert result.identity.model == "LS-WVL(KOUGYOKU)"
    assert result.unique_id == "host:nas.local"
    modern.shutdown.assert_not_awaited()
    modern.reboot.assert_not_awaited()
    legacy.shutdown.assert_not_awaited()
    legacy.reboot.assert_not_awaited()


async def test_invalid_modern_credentials_do_not_switch_protocol(hass):
    modern = MagicMock()
    modern.prepare_update = AsyncMock(
        side_effect=BuffaloAuthenticationError("rejected")
    )
    legacy = MagicMock()

    with (
        patch(
            "custom_components.buffalo_linkstation.config_flow.BuffaloLinkStationApi",
            return_value=modern,
        ),
        patch(
            "custom_components.buffalo_linkstation.config_flow.LegacyLinkStationApi",
            return_value=legacy,
        ),
        pytest.raises(BuffaloAuthenticationError),
    ):
        await _async_validate(hass, INPUT)

    legacy.prepare_update.assert_not_called()


async def test_user_flow_stores_detected_legacy_protocol(hass):
    legacy = ValidationResult(
        BuffaloIdentity(model="LS-WVL(KOUGYOKU)"),
        "host:nas.local",
        PROTOCOL_LEGACY_CGI,
    )
    with patch(
        "custom_components.buffalo_linkstation.config_flow._async_validate",
        return_value=legacy,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=INPUT,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROTOCOL] == PROTOCOL_LEGACY_CGI
    assert result["data"][CONF_DEVICE_ID] == "host:nas.local"
    assert result["result"].unique_id == "host:nas.local"


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
    assert result["type"] is FlowResultType.FORM
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
    assert first["type"] is FlowResultType.CREATE_ENTRY
    assert second["type"] is FlowResultType.ABORT
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


async def test_legacy_reconfigure_updates_host_without_changing_identity(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:old-nas.local",
        data={
            **INPUT,
            CONF_HOST: "old-nas.local",
            CONF_DEVICE_ID: "host:old-nas.local",
            CONF_PROTOCOL: PROTOCOL_LEGACY_CGI,
        },
        options={CONF_ENABLE_SHUTDOWN: True, CONF_ENABLE_REBOOT: False},
    )
    entry.add_to_hass(hass)
    result_identity = ValidationResult(
        BuffaloIdentity(model="LS-WVL(KOUGYOKU)"),
        "host:new-nas.local",
        PROTOCOL_LEGACY_CGI,
    )

    with (
        patch(
            "custom_components.buffalo_linkstation.config_flow._async_validate",
            return_value=result_identity,
        ),
        patch.object(
            hass.config_entries,
            "async_reload",
            new=AsyncMock(return_value=True),
        ) as reload_entry,
    ):
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"],
            user_input={**INPUT, CONF_HOST: "new-nas.local"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "host:old-nas.local"
    assert entry.data[CONF_DEVICE_ID] == "host:old-nas.local"
    assert entry.data[CONF_HOST] == "new-nas.local"
    assert entry.data[CONF_PROTOCOL] == PROTOCOL_LEGACY_CGI
    assert entry.options[CONF_ENABLE_SHUTDOWN] is True
    assert entry.options[CONF_ENABLE_REBOOT] is False
    reload_entry.assert_awaited_once_with(entry.entry_id)


async def test_current_host_prevents_duplicate_after_legacy_reconfigure(hass):
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:old-nas.local",
        data={
            **INPUT,
            CONF_HOST: "new-nas.local",
            CONF_DEVICE_ID: "host:old-nas.local",
            CONF_PROTOCOL: PROTOCOL_LEGACY_CGI,
        },
    )
    existing.add_to_hass(hass)
    candidate = ValidationResult(
        BuffaloIdentity(model="LS-WVL(KOUGYOKU)"),
        "host:new-nas.local",
        PROTOCOL_LEGACY_CGI,
    )

    with patch(
        "custom_components.buffalo_linkstation.config_flow._async_validate",
        return_value=candidate,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={**INPUT, CONF_HOST: "new-nas.local"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert existing.unique_id == "host:old-nas.local"


async def test_legacy_reauth_accepts_current_host_without_rewriting_identity(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:old-nas.local",
        data={
            **INPUT,
            CONF_HOST: "new-nas.local",
            CONF_DEVICE_ID: "host:old-nas.local",
            CONF_PROTOCOL: PROTOCOL_LEGACY_CGI,
        },
    )
    entry.add_to_hass(hass)
    current = ValidationResult(
        BuffaloIdentity(model="LS-WVL(KOUGYOKU)"),
        "host:new-nas.local",
        PROTOCOL_LEGACY_CGI,
    )

    with (
        patch(
            "custom_components.buffalo_linkstation.config_flow._async_validate",
            return_value=current,
        ),
        patch.object(
            hass.config_entries,
            "async_reload",
            new=AsyncMock(return_value=True),
        ),
    ):
        initial = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            initial["flow_id"],
            user_input={CONF_USERNAME: "admin", CONF_PASSWORD: "new-secret"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == "host:old-nas.local"
    assert entry.data[CONF_DEVICE_ID] == "host:old-nas.local"
    assert entry.data[CONF_HOST] == "new-nas.local"


async def _options_defaults(hass, *, model, firmware, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"serial:{model.casefold()}",
        data=INPUT,
        options=options or {},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.coordinator.data.identity = BuffaloIdentity(
        model=model,
        firmware=firmware,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    return result["data_schema"]({})


@pytest.mark.parametrize(
    ("model", "firmware"),
    [
        ("LS220D (SANJO)", "1.86 - 0.07"),
        ("LS420D (LS-WM)", "1.88 - 0.03"),
    ],
)
async def test_options_default_shutdown_enabled_for_verified_hardware(
    hass, model, firmware
):
    defaults = await _options_defaults(
        hass,
        model=model,
        firmware=firmware,
    )

    assert defaults[CONF_ENABLE_SHUTDOWN] is True
    assert defaults[CONF_ENABLE_REBOOT] is False


async def test_options_default_power_controls_disabled_for_unverified_hardware(hass):
    defaults = await _options_defaults(hass, model="LS999D", firmware="9.9")

    assert defaults[CONF_ENABLE_SHUTDOWN] is False
    assert defaults[CONF_ENABLE_REBOOT] is False


async def test_options_default_both_power_controls_disabled_for_ls_wvl(hass):
    defaults = await _options_defaults(
        hass,
        model="LS-WVL(KOUGYOKU)",
        firmware=None,
    )

    assert defaults[CONF_ENABLE_SHUTDOWN] is False
    assert defaults[CONF_ENABLE_REBOOT] is False


async def test_options_preserve_explicit_power_control_values(hass):
    defaults = await _options_defaults(
        hass,
        model="LS220D",
        firmware="1.86",
        options={CONF_ENABLE_SHUTDOWN: False, CONF_ENABLE_REBOOT: True},
    )

    assert defaults[CONF_ENABLE_SHUTDOWN] is False
    assert defaults[CONF_ENABLE_REBOOT] is True


async def test_saving_power_options_does_not_execute_power_commands(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        data=INPUT,
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock()
    entry.runtime_data.coordinator.data.identity = BuffaloIdentity(
        model="LS220D",
        firmware="1.86",
    )
    entry.runtime_data.api.shutdown = AsyncMock()
    entry.runtime_data.api.reboot = AsyncMock()
    initial = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        initial["flow_id"],
        user_input={
            CONF_UPDATE_INTERVAL: 60,
            CONF_TIMEOUT: 10,
            CONF_ENABLE_SHUTDOWN: False,
            CONF_ENABLE_REBOOT: True,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENABLE_SHUTDOWN] is False
    assert result["data"][CONF_ENABLE_REBOOT] is True
    entry.runtime_data.api.shutdown.assert_not_awaited()
    entry.runtime_data.api.reboot.assert_not_awaited()
