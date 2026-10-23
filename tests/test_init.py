"""Tests for integration unload behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation import (
    BuffaloLinkStationRuntimeData,
    async_unload_entry,
)
from custom_components.buffalo_linkstation.const import DOMAIN, PLATFORMS


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
