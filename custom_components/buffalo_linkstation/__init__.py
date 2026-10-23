"""The Buffalo LinkStation integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .api import (
    BuffaloAuthenticationError,
    BuffaloLinkStationApi,
    BuffaloLinkStationError,
)
from .const import (
    CONF_PROTOCOL,
    CONF_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    PLATFORMS,
    PROTOCOL_JSON_RPC,
    PROTOCOL_LEGACY_CGI,
)
from .legacy_api import LegacyLinkStationApi
from .models import BuffaloLinkStationClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BuffaloLinkStationCoordinator


@dataclass(slots=True)
class BuffaloLinkStationRuntimeData:
    """Runtime objects owned by one config entry."""

    api: BuffaloLinkStationClient
    coordinator: BuffaloLinkStationCoordinator


if TYPE_CHECKING:
    type BuffaloLinkStationConfigEntry = ConfigEntry[BuffaloLinkStationRuntimeData]
else:
    type BuffaloLinkStationConfigEntry = Any


def create_api(hass: HomeAssistant, entry: ConfigEntry) -> BuffaloLinkStationClient:
    """Create an API client backed by Home Assistant's shared HTTP session."""
    from homeassistant.const import (  # noqa: PLC0415
        CONF_HOST,
        CONF_PASSWORD,
        CONF_USERNAME,
    )
    from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
        async_get_clientsession,
    )

    api_class = (
        LegacyLinkStationApi
        if entry.data.get(CONF_PROTOCOL, PROTOCOL_JSON_RPC) == PROTOCOL_LEGACY_CGI
        else BuffaloLinkStationApi
    )
    return api_class(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        timeout=entry.options.get(
            CONF_TIMEOUT, entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: BuffaloLinkStationConfigEntry
) -> bool:
    """Set up one Buffalo LinkStation."""
    from homeassistant.exceptions import (  # noqa: PLC0415
        ConfigEntryAuthFailed,
        ConfigEntryNotReady,
    )

    from .coordinator import BuffaloLinkStationCoordinator  # noqa: PLC0415

    api = create_api(hass, entry)
    coordinator = BuffaloLinkStationCoordinator(
        hass,
        entry,
        api,
        timedelta(
            seconds=entry.options.get(
                CONF_UPDATE_INTERVAL,
                entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            )
        ),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        raise
    except BuffaloAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain="buffalo_linkstation",
            translation_key="invalid_auth",
        ) from err
    except BuffaloLinkStationError as err:
        raise ConfigEntryNotReady(
            translation_domain="buffalo_linkstation",
            translation_key="cannot_connect",
        ) from err

    entry.runtime_data = BuffaloLinkStationRuntimeData(api, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BuffaloLinkStationConfigEntry
) -> bool:
    """Unload one Buffalo LinkStation without closing the shared HTTP session."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(
    hass: HomeAssistant, entry: BuffaloLinkStationConfigEntry
) -> bool:
    """Mark pre-legacy entries as JSON-RPC without changing identity or options."""
    if entry.version > 1:
        return True
    data = {**entry.data, CONF_PROTOCOL: PROTOCOL_JSON_RPC}
    hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after credentials, address, or options change."""
    await hass.config_entries.async_reload(entry.entry_id)
