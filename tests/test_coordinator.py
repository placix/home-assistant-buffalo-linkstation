"""Tests for coordinator behavior and entity availability."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation.api import (
    BuffaloAuthenticationError,
    BuffaloConnectionError,
    BuffaloJsonRpcError,
)
from custom_components.buffalo_linkstation.binary_sensor import (
    BINARY_SENSORS,
    BuffaloLinkStationBinarySensor,
)
from custom_components.buffalo_linkstation.const import DOMAIN
from custom_components.buffalo_linkstation.coordinator import (
    BuffaloLinkStationCoordinator,
)


def make_api():
    api = MagicMock()
    api.host = "nas.local"
    api.prepare_update = AsyncMock()
    api.get_system_info = AsyncMock(
        return_value={
            "hostname": "office-nas",
            "model": "LS220D",
            "firmware": "1.86",
            "serial_number": "ABC",
        }
    )
    api.get_features = AsyncMock(return_value={"SUPPORT_REBOOT_FROMWEB": 1})
    api.get_devices = AsyncMock(
        return_value=[{"device_type": "array", "status": ["fine"]}]
    )
    api.get_shares = AsyncMock(return_value=[])
    return api


async def test_successful_update_and_recovery(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    entry.add_to_hass(hass)
    api = make_api()
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))

    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.data.identity.model == "LS220D"

    api.get_system_info.side_effect = BuffaloConnectionError("offline")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success

    api.get_system_info.side_effect = None
    await coordinator.async_refresh()
    assert coordinator.last_update_success


async def test_authentication_failure_requests_reauth(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    entry.add_to_hass(hass)
    api = make_api()
    api.get_system_info.side_effect = BuffaloAuthenticationError("expired")
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))

    with pytest.raises(Exception, match="invalid_auth"):
        await coordinator._async_update_data()


async def test_feature_failure_preserves_monitoring_and_retries(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    entry.add_to_hass(hass)
    api = make_api()
    api.get_features.side_effect = [
        BuffaloJsonRpcError(-32602, "Invalid Params"),
        {"SUPPORT_REBOOT_FROMWEB": 1},
    ]
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert first.system_info["model"] == "LS220D"
    assert first.devices
    assert first.features == {}
    assert second.features == {"SUPPORT_REBOOT_FROMWEB": 1}
    assert api.get_features.await_count == 2


async def test_transient_missing_identity_fields_preserve_last_known_identity(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    entry.add_to_hass(hass)
    api = make_api()
    api.get_system_info.side_effect = [
        {
            "hostname": "office-nas",
            "product_name": "LS220D",
            "fw_version_major": "1.86",
            "serial_number": "ABC",
        },
        {"hostname": "office-nas"},
    ]
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))

    first = await coordinator._async_update_data()
    second = await coordinator._async_update_data()

    assert first.identity.model == "LS220D"
    assert second.identity.model == "LS220D"
    assert second.identity.firmware == "1.86"
    assert second.identity.serial_number == "ABC"


async def test_unreachable_entity_is_unavailable_not_off(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    entry.add_to_hass(hass)
    api = make_api()
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))
    await coordinator.async_refresh()
    reachable = BuffaloLinkStationBinarySensor(entry, coordinator, BINARY_SENSORS[0])
    assert reachable.available
    assert reachable.is_on is True

    coordinator.async_set_update_error(UpdateFailed("offline"))
    assert not reachable.available
    # The last state is not changed to False, which would imply a clean shutdown.
    assert reachable.is_on is True
