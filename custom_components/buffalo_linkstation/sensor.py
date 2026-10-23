"""Sensors for Buffalo LinkStation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BuffaloLinkStationConfigEntry, parsing
from .coordinator import (
    BuffaloLinkStationCoordinator,
    BuffaloLinkStationData,
)
from .entity import BuffaloLinkStationEntity


@dataclass(frozen=True, kw_only=True)
class BuffaloSensorDescription(SensorEntityDescription):
    """Describe a sensor backed by a safe extraction function."""

    value_fn: Callable[[BuffaloLinkStationData], str | float | None]


SENSORS: tuple[BuffaloSensorDescription, ...] = (
    BuffaloSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.firmware,
    ),
    BuffaloSensorDescription(
        key="cpu_status",
        translation_key="cpu_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.cpu_status,
    ),
    BuffaloSensorDescription(
        key="system_errors",
        translation_key="system_errors",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.system_errors,
    ),
    BuffaloSensorDescription(
        key="system_messages",
        translation_key="system_messages",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.system_messages,
    ),
    BuffaloSensorDescription(
        key="storage_utilization",
        translation_key="storage_utilization",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=parsing.utilization,
    ),
    BuffaloSensorDescription(
        key="storage_used",
        translation_key="storage_used",
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=parsing.used_storage,
    ),
    BuffaloSensorDescription(
        key="storage_total",
        translation_key="storage_total",
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=parsing.total_storage,
    ),
    BuffaloSensorDescription(
        key="disk_status",
        translation_key="disk_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.disk_status,
    ),
    BuffaloSensorDescription(
        key="raid_status",
        translation_key="raid_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsing.raid_status,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: BuffaloLinkStationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up safe, capability-sensitive NAS sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        BuffaloLinkStationSensor(entry, coordinator, description)
        for description in SENSORS
        if description.value_fn(coordinator.data) is not None
    )


class BuffaloLinkStationSensor(BuffaloLinkStationEntity, SensorEntity):
    """A coordinator-backed LinkStation sensor."""

    entity_description: BuffaloSensorDescription

    def __init__(
        self,
        entry: BuffaloLinkStationConfigEntry,
        coordinator: BuffaloLinkStationCoordinator,
        description: BuffaloSensorDescription,
    ) -> None:
        super().__init__(entry, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

    @property
    def native_value(self) -> str | float | None:
        """Return the value from the latest successful snapshot."""
        return self.entity_description.value_fn(self.coordinator.data)
