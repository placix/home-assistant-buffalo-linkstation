"""Base entity for Buffalo LinkStation."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BuffaloLinkStationConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import BuffaloLinkStationCoordinator


class BuffaloLinkStationEntity(CoordinatorEntity[BuffaloLinkStationCoordinator]):
    """Entity attached to one LinkStation device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: BuffaloLinkStationConfigEntry,
        coordinator: BuffaloLinkStationCoordinator,
    ) -> None:
        """Initialize the entity and its device registry metadata."""
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return current device metadata."""
        identity = self.coordinator.data.identity
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=identity.model,
            name=identity.hostname or self._entry.title,
            serial_number=identity.serial_number,
            sw_version=identity.firmware,
            configuration_url=self.coordinator.api.endpoint,
        )
