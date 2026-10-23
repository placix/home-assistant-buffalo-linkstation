"""The Buffalo LinkStation integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .api import (
    BuffaloAuthenticationError,
    BuffaloConnectionError,
    BuffaloInvalidResponseError,
    BuffaloJsonRpcError,
    BuffaloLinkStationApi,
    BuffaloTimeoutError,
)
from .const import (
    CONF_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    PLATFORMS,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BuffaloLinkStationCoordinator


@dataclass(slots=True)
class BuffaloLinkStationRuntimeData:
    """Runtime objects owned by one config entry."""

    api: BuffaloLinkStationApi
    coordinator: BuffaloLinkStationCoordinator


if TYPE_CHECKING:
    type BuffaloLinkStationConfigEntry = ConfigEntry[BuffaloLinkStationRuntimeData]
else:
    type BuffaloLinkStationConfigEntry = Any


def create_api(hass: HomeAssistant, entry: ConfigEntry) -> BuffaloLinkStationApi:
    """Create an API client backed by Home Assistant's shared HTTP session."""
    from homeassistant.const import (  # noqa: PLC0415
        CONF_HOST,
        CONF_PASSWORD,
        CONF_USERNAME,
    )
    from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
        async_get_clientsession,
    )

    return BuffaloLinkStationApi(
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
    except (
        BuffaloConnectionError,
        BuffaloTimeoutError,
        BuffaloInvalidResponseError,
        BuffaloJsonRpcError,
    ) as err:
        raise ConfigEntryNotReady(
            translation_domain="buffalo_linkstation",
            translation_key="cannot_connect",
        ) from err
    except BuffaloAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            translation_domain="buffalo_linkstation",
            translation_key="invalid_auth",
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


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload after credentials, address, or options change."""
    await hass.config_entries.async_reload(entry.entry_id)
