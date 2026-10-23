"""Tests for explicit power controls."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation.api import BuffaloIdentity
from custom_components.buffalo_linkstation.button import (
    BUTTONS,
    BuffaloLinkStationButton,
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
