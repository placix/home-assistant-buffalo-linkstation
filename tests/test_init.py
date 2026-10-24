"""Tests for integration unload behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation import (
    BuffaloLinkStationRuntimeData,
    async_migrate_entry,
    async_unload_entry,
    create_api,
)
from custom_components.buffalo_linkstation.api import BuffaloLinkStationApi
from custom_components.buffalo_linkstation.const import (
    CONF_DEVICE_ID,
    CONF_PROTOCOL,
    DOMAIN,
    PLATFORMS,
    PROTOCOL_JSON_RPC,
    PROTOCOL_LEGACY_CGI,
)
from custom_components.buffalo_linkstation.legacy_api import LegacyLinkStationApi


async def test_unload_all_platforms_without_closing_shared_session(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:abc")
    api = MagicMock()
    coordinator = MagicMock()
    entry.runtime_data = BuffaloLinkStationRuntimeData(api, coordinator)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=True),
    ) as unload:
        assert await async_unload_entry(hass, entry)

    unload.assert_awaited_once_with(entry, PLATFORMS)
    assert not hasattr(api, "close") or not api.close.called


async def test_version_one_entry_migrates_to_explicit_json_rpc_without_option_changes(
    hass,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial:abc",
        data={"host": "nas.local", "username": "admin", "password": "secret"},
        options={"enable_shutdown": False, "enable_reboot": True},
        version=1,
    )
    entry.add_to_hass(hass)
    original_options = dict(entry.options)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert entry.data[CONF_PROTOCOL] == PROTOCOL_JSON_RPC
    assert entry.unique_id == "serial:abc"
    assert entry.options == original_options


async def test_version_one_host_identity_migration_does_not_rewrite_unique_id(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:legacy-address.local",
        data={
            "host": "legacy-address.local",
            "username": "admin",
            "password": "secret",
        },
        options={"enable_shutdown": False},
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert entry.unique_id == "host:legacy-address.local"
    assert entry.data[CONF_PROTOCOL] == PROTOCOL_JSON_RPC
    assert CONF_DEVICE_ID not in entry.data
    assert entry.options == {"enable_shutdown": False}


async def test_client_factory_uses_stored_protocol_and_modern_default(
    hass,
):
    base_data = {
        "host": "nas.local",
        "username": "admin",
        "password": "secret",
    }
    modern_entry = MockConfigEntry(domain=DOMAIN, data=base_data)
    legacy_entry = MockConfigEntry(
        domain=DOMAIN,
        data={**base_data, CONF_PROTOCOL: PROTOCOL_LEGACY_CGI},
    )
    session = MagicMock()

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        modern = create_api(hass, modern_entry)
        legacy = create_api(hass, legacy_entry)

    assert isinstance(modern, BuffaloLinkStationApi)
    assert isinstance(legacy, LegacyLinkStationApi)


async def test_legacy_reload_uses_new_host_and_preserves_stable_identity(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:original-address.local",
        data={
            "host": "new-address.local",
            "username": "admin",
            "password": "secret",
            CONF_DEVICE_ID: "host:original-address.local",
            CONF_PROTOCOL: PROTOCOL_LEGACY_CGI,
        },
    )
    session = MagicMock()

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        return_value=session,
    ):
        api = create_api(hass, entry)

    assert isinstance(api, LegacyLinkStationApi)
    assert api.host == "new-address.local"
    assert entry.unique_id == "host:original-address.local"
    assert entry.data[CONF_DEVICE_ID] == "host:original-address.local"
