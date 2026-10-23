"""Explicit shutdown and reboot controls for Buffalo LinkStation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BuffaloLinkStationConfigEntry
from .api import BuffaloLinkStationError
from .const import (
    CONF_ENABLE_REBOOT,
    CONF_ENABLE_SHUTDOWN,
    is_verified_shutdown,
)
from .coordinator import BuffaloLinkStationCoordinator
from .entity import BuffaloLinkStationEntity


@dataclass(frozen=True, kw_only=True)
class BuffaloButtonDescription(ButtonEntityDescription):
    """Describe one destructive control."""

    method_name: str


BUTTONS = (
    BuffaloButtonDescription(
        key="shutdown",
        translation_key="shutdown",
        icon="mdi:power",
        entity_category=EntityCategory.CONFIG,
        method_name="shutdown",
    ),
    BuffaloButtonDescription(
        key="reboot",
        translation_key="reboot",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
        method_name="reboot",
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: BuffaloLinkStationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add controls; unsupported controls remain unavailable, never probed."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        BuffaloLinkStationButton(entry, coordinator, description)
        for description in BUTTONS
    )


class BuffaloLinkStationButton(BuffaloLinkStationEntity, ButtonEntity):
    """A serialized, user-triggered NAS power action."""

    entity_description: BuffaloButtonDescription

    def __init__(
        self,
        entry: BuffaloLinkStationConfigEntry,
        coordinator: BuffaloLinkStationCoordinator,
        description: BuffaloButtonDescription,
    ) -> None:
        super().__init__(entry, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Expose controls only when the NAS is reachable and support is explicit."""
        if not super().available or self.coordinator.control_lock.locked():
            return False
        if self.entity_description.key == "shutdown":
            identity = self.coordinator.data.identity
            return is_verified_shutdown(identity.model, identity.firmware) or bool(
                self._entry.options.get(CONF_ENABLE_SHUTDOWN, False)
            )
        return bool(self._entry.options.get(CONF_ENABLE_REBOOT, False))

    async def async_press(self) -> None:
        """Execute one explicit operation without retrying ambiguous failures."""
        if not self.available:
            raise HomeAssistantError(
                translation_domain="buffalo_linkstation",
                translation_key="operation_not_available",
            )
        method: Callable[[], Awaitable[Any]] = getattr(
            self.coordinator.api, self.entity_description.method_name
        )
        async with self.coordinator.control_lock:
            try:
                await method()
            except BuffaloLinkStationError as err:
                raise HomeAssistantError(
                    translation_domain="buffalo_linkstation",
                    translation_key="operation_failed",
                ) from err
