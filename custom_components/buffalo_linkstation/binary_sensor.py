"""Binary sensors for Buffalo LinkStation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BuffaloLinkStationConfigEntry, parsing
from .coordinator import BuffaloLinkStationCoordinator, BuffaloLinkStationData
from .entity import BuffaloLinkStationEntity


@dataclass(frozen=True, kw_only=True)
class BuffaloBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a binary sensor extraction function."""

    value_fn: Callable[[BuffaloLinkStationData], bool | None]


BINARY_SENSORS: tuple[BuffaloBinarySensorDescription, ...] = (
    BuffaloBinarySensorDescription(
        key="reachable",
        translation_key="reachable",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _data: True,
    ),
    BuffaloBinarySensorDescription(
        key="system_error",
        translation_key="system_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.system_problem,
    ),
    BuffaloBinarySensorDescription(
        key="raid_problem",
        translation_key="raid_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.raid_problem,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: BuffaloLinkStationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors supported by the current response."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        BuffaloLinkStationBinarySensor(entry, coordinator, description)
        for description in BINARY_SENSORS
        if description.key == "reachable"
        or description.value_fn(coordinator.data) is not None
    )


class BuffaloLinkStationBinarySensor(BuffaloLinkStationEntity, BinarySensorEntity):
    """A coordinator-backed LinkStation binary sensor."""

    entity_description: BuffaloBinarySensorDescription

    def __init__(
        self,
        entry: BuffaloLinkStationConfigEntry,
        coordinator: BuffaloLinkStationCoordinator,
        description: BuffaloBinarySensorDescription,
    ) -> None:
        super().__init__(entry, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return a problem/connectivity state from the last good snapshot."""
        return self.entity_description.value_fn(self.coordinator.data)
