"""Data coordinator for Buffalo LinkStation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BuffaloAuthenticationError,
    BuffaloIdentity,
    BuffaloJsonRpcError,
    BuffaloLinkStationError,
    extract_identity,
)
from .const import DOMAIN
from .models import BuffaloLinkStationClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BuffaloLinkStationData:
    """One atomic polling snapshot."""

    system_info: dict[str, Any]
    features: dict[str, Any]
    devices: tuple[dict[str, Any], ...]
    identity: BuffaloIdentity


class BuffaloLinkStationCoordinator(DataUpdateCoordinator[BuffaloLinkStationData]):
    """Poll a single NAS without mixing data between config entries."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: BuffaloLinkStationClient,
        update_interval: timedelta,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.api = api
        self.control_lock = asyncio.Lock()
        self._features: dict[str, Any] | None = None
        self._identity: BuffaloIdentity | None = None

    async def _async_update_data(self) -> BuffaloLinkStationData:
        try:
            await self.api.prepare_update()
        except BuffaloAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except BuffaloLinkStationError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err
        features = self._features or {}
        if self._features is None:
            try:
                features = await self.api.get_features()
            except BuffaloLinkStationError as err:
                # Feature flags are optional metadata. Preserve portal/device
                # monitoring and retry this independent query next update.
                if isinstance(err, BuffaloJsonRpcError):
                    _LOGGER.warning(
                        "Unable to update optional NAS feature flags "
                        "(JSON-RPC code %s)",
                        err.code,
                    )
                else:
                    _LOGGER.warning(
                        "Unable to update optional NAS feature flags (%s)",
                        type(err).__name__,
                    )
            else:
                self._features = features
        try:
            system_info = await self.api.get_system_info()
            devices = await self.api.get_devices()
        except BuffaloAuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except BuffaloLinkStationError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err
        observed_identity = extract_identity(system_info, self.api.host, features)
        previous_identity = self._identity
        identity = BuffaloIdentity(
            hostname=observed_identity.hostname,
            model=observed_identity.model
            or (previous_identity.model if previous_identity else None),
            firmware=observed_identity.firmware
            or (previous_identity.firmware if previous_identity else None),
            serial_number=observed_identity.serial_number
            or (previous_identity.serial_number if previous_identity else None),
        )
        self._identity = identity
        return BuffaloLinkStationData(
            system_info=system_info,
            features=features,
            devices=tuple(devices),
            identity=identity,
        )
