"""Tests for explicit power controls."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation.api import BuffaloIdentity
from custom_components.buffalo_linkstation.button import (
    BUTTONS,
    BuffaloLinkStationButton,
    async_setup_entry,
)
from custom_components.buffalo_linkstation.const import (
    CONF_ENABLE_REBOOT,
    CONF_ENABLE_SHUTDOWN,
    DOMAIN,
)
from custom_components.buffalo_linkstation.coordinator import (
    BuffaloLinkStationCoordinator,
    BuffaloLinkStationData,
)


def make_coordinator(hass, entry, *, model="LS220D", firmware="1.86", features=None):
    api = MagicMock()
    api.host = "nas.local"
    api.endpoint = "http://nas.local/nasapi/"
    api.shutdown = AsyncMock()
    api.reboot = AsyncMock()
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))
    coordinator.async_set_updated_data(
        BuffaloLinkStationData(
            system_info={},
            features=features or {"SUPPORT_REBOOT_FROMWEB": 1},
            devices=(),
            identity=BuffaloIdentity(
                model=model,
                firmware=firmware,
                hostname="nas",
            ),
        )
    )
    return coordinator


async def test_shutdown_only_runs_after_press(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(hass, entry)
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[0])
    coordinator.api.shutdown.assert_not_awaited()
    assert button.available

    await button.async_press()
    coordinator.api.shutdown.assert_awaited_once_with()


async def test_both_power_entities_are_always_created(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        options={CONF_ENABLE_SHUTDOWN: False, CONF_ENABLE_REBOOT: False},
    )
    coordinator = make_coordinator(hass, entry)
    entry.runtime_data = MagicMock(coordinator=coordinator)
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    entities = list(add_entities.call_args.args[0])
    assert [entity.entity_description.key for entity in entities] == [
        "shutdown",
        "reboot",
    ]
    assert [entity.unique_id for entity in entities] == [
        "serial:abc_shutdown",
        "serial:abc_reboot",
    ]
    assert not any(entity.available for entity in entities)
    coordinator.api.shutdown.assert_not_awaited()
    coordinator.api.reboot.assert_not_awaited()


def test_verified_shutdown_available_on_ls220d_despite_zero_feature_flag(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(
        hass,
        entry,
        model="LS220D",
        firmware="1.86",
        features={"SUPPORT_SHUTDOWN_FROMWEB": 0},
    )
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[0])
    assert button.available


def test_both_confirmed_ls220d_identity_formats_enable_shutdown(hass):
    standard_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:first",
    )
    alternate_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:second",
    )
    standard = BuffaloLinkStationButton(
        standard_entry,
        make_coordinator(hass, standard_entry, model=" LS220D ", firmware=" 1.86 "),
        BUTTONS[0],
    )
    alternate = BuffaloLinkStationButton(
        alternate_entry,
        make_coordinator(
            hass,
            alternate_entry,
            model="LS220D (SANJO)",
            firmware="1.86 - 0.07",
        ),
        BUTTONS[0],
    )

    assert standard.available
    assert alternate.available
    assert alternate.extra_state_attributes == {
        "protocol": "json_rpc",
        "reported_model": "LS220D (SANJO)",
        "reported_firmware": "1.86 - 0.07",
        "recognized_model": "LS220D",
        "recognized_firmware": "1.86",
        "availability_reason": "available_verified_default",
    }


def test_verified_shutdown_available_on_ls420d(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(
        hass,
        entry,
        model="LS420D",
        firmware="1.88",
        features={"SUPPORT_SHUTDOWN_FROMWEB": 0},
    )
    assert BuffaloLinkStationButton(entry, coordinator, BUTTONS[0]).available


def test_unverified_model_or_firmware_requires_explicit_opt_in(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(
        hass,
        entry,
        model="LS420D",
        firmware="1.89",
        features={"SUPPORT_SHUTDOWN_FROMWEB": 1},
    )
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[0])
    assert not button.available

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:def",
        options={CONF_ENABLE_SHUTDOWN: True},
    )
    coordinator = make_coordinator(hass, entry, model="unverified", firmware="9.9")
    assert BuffaloLinkStationButton(entry, coordinator, BUTTONS[0]).available


def test_ls_wvl_power_controls_default_disabled_and_can_be_explicitly_enabled(hass):
    disabled_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:legacy.local",
    )
    disabled_coordinator = make_coordinator(
        hass,
        disabled_entry,
        model="LS-WVL(KOUGYOKU)",
        firmware=None,
    )
    assert not any(
        BuffaloLinkStationButton(
            disabled_entry, disabled_coordinator, description
        ).available
        for description in BUTTONS
    )

    enabled_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:legacy.local",
        options={CONF_ENABLE_SHUTDOWN: True, CONF_ENABLE_REBOOT: True},
    )
    enabled_coordinator = make_coordinator(
        hass,
        enabled_entry,
        model="LS-WVL(KOUGYOKU)",
        firmware=None,
    )
    enabled = [
        BuffaloLinkStationButton(enabled_entry, enabled_coordinator, description)
        for description in BUTTONS
    ]
    assert all(button.available for button in enabled)
    assert [button.unique_id for button in enabled] == [
        "host:legacy.local_shutdown",
        "host:legacy.local_reboot",
    ]
    enabled_coordinator.api.shutdown.assert_not_awaited()
    enabled_coordinator.api.reboot.assert_not_awaited()


def test_explicit_shutdown_disable_overrides_verified_default(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        options={CONF_ENABLE_SHUTDOWN: False},
    )
    coordinator = make_coordinator(hass, entry, model="LS220D", firmware="1.86")
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[0])

    assert not button.available
    assert button.extra_state_attributes["availability_reason"] == "shutdown_disabled"
    coordinator.api.shutdown.assert_not_awaited()


def test_power_options_change_only_availability_and_preserve_unique_ids(hass):
    disabled_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        options={CONF_ENABLE_SHUTDOWN: False, CONF_ENABLE_REBOOT: False},
    )
    enabled_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        options={CONF_ENABLE_SHUTDOWN: True, CONF_ENABLE_REBOOT: True},
    )
    disabled_coordinator = make_coordinator(hass, disabled_entry)
    enabled_coordinator = make_coordinator(hass, enabled_entry)
    disabled = [
        BuffaloLinkStationButton(disabled_entry, disabled_coordinator, description)
        for description in BUTTONS
    ]
    enabled = [
        BuffaloLinkStationButton(enabled_entry, enabled_coordinator, description)
        for description in BUTTONS
    ]

    assert [entity.unique_id for entity in disabled] == [
        entity.unique_id for entity in enabled
    ]
    assert not any(entity.available for entity in disabled)
    assert all(entity.available for entity in enabled)
    disabled_coordinator.api.shutdown.assert_not_awaited()
    disabled_coordinator.api.reboot.assert_not_awaited()
    enabled_coordinator.api.shutdown.assert_not_awaited()
    enabled_coordinator.api.reboot.assert_not_awaited()


def test_legacy_address_change_preserves_power_entity_unique_ids(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:original-address.local",
        data={"host": "original-address.local", "protocol": "legacy_cgi"},
        options={CONF_ENABLE_SHUTDOWN: True, CONF_ENABLE_REBOOT: True},
    )
    entry.add_to_hass(hass)
    before_coordinator = make_coordinator(
        hass,
        entry,
        model="LS-WVL(KOUGYOKU)",
        firmware=None,
    )
    before = [
        BuffaloLinkStationButton(entry, before_coordinator, description).unique_id
        for description in BUTTONS
    ]

    hass.config_entries.async_update_entry(
        entry,
        data={"host": "new-address.local", "protocol": "legacy_cgi"},
    )
    after_coordinator = make_coordinator(
        hass,
        entry,
        model="LS-WVL(KOUGYOKU)",
        firmware=None,
    )
    after = [
        BuffaloLinkStationButton(entry, after_coordinator, description).unique_id
        for description in BUTTONS
    ]

    assert (
        before
        == after
        == [
            "host:original-address.local_shutdown",
            "host:original-address.local_reboot",
        ]
    )


async def test_reboot_request(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        options={CONF_ENABLE_REBOOT: True},
    )
    coordinator = make_coordinator(hass, entry)
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[1])
    assert button.available
    await button.async_press()
    coordinator.api.reboot.assert_awaited_once_with()


def test_reboot_requires_opt_in_despite_webui_feature_flag(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(hass, entry)
    assert not BuffaloLinkStationButton(entry, coordinator, BUTTONS[1]).available


def test_both_buttons_unavailable_when_coordinator_is_unavailable(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        options={CONF_ENABLE_SHUTDOWN: True, CONF_ENABLE_REBOOT: True},
    )
    coordinator = make_coordinator(hass, entry)
    coordinator.async_set_update_error(UpdateFailed("offline"))

    for description in BUTTONS:
        button = BuffaloLinkStationButton(entry, coordinator, description)
        assert not button.available
        assert (
            button.extra_state_attributes["availability_reason"]
            == "coordinator_unavailable"
        )


async def test_shutdown_reports_operation_lock_as_temporary_unavailability(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(hass, entry)
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[0])

    async with coordinator.control_lock:
        assert not button.available
        assert (
            button.extra_state_attributes["availability_reason"]
            == "operation_in_progress"
        )

    assert button.available


def test_shutdown_reports_failed_coordinator_update(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    coordinator = make_coordinator(hass, entry)
    button = BuffaloLinkStationButton(entry, coordinator, BUTTONS[0])

    coordinator.async_set_update_error(UpdateFailed("offline"))

    assert not button.available
    assert (
        button.extra_state_attributes["availability_reason"]
        == "coordinator_unavailable"
    )
