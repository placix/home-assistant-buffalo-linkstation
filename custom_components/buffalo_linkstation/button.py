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
    CONF_ENABLE_SHUTDOWN,
    CONF_PROTOCOL,
    PROTOCOL_JSON_RPC,
    is_reboot_enabled,
    is_shutdown_enabled,
    normalize_firmware,
    normalize_model,
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
        return self._availability_reason().startswith("available_")

    def _availability_reason(self) -> str:
        """Return a credential-free explanation of control availability."""
        if not super().available:
            reason = "coordinator_unavailable"
        elif self.coordinator.control_lock.locked():
            reason = "operation_in_progress"
        elif self.entity_description.key == "shutdown":
            identity = self.coordinator.data.identity
            if is_shutdown_enabled(
                self._entry.options, identity.model, identity.firmware
            ):
                reason = (
                    "available_enabled"
                    if CONF_ENABLE_SHUTDOWN in self._entry.options
                    else "available_verified_default"
                )
            else:
                reason = (
                    "shutdown_disabled"
                    if CONF_ENABLE_SHUTDOWN in self._entry.options
                    else "shutdown_disabled_unverified_default"
                )
        elif is_reboot_enabled(self._entry.options):
            reason = "available_enabled"
        else:
            reason = "reboot_disabled"
        return reason

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose only non-sensitive identity and availability diagnostics."""
        identity = self.coordinator.data.identity
        normalized_model = normalize_model(identity.model)
        return {
            "protocol": self._entry.data.get(CONF_PROTOCOL, PROTOCOL_JSON_RPC),
            "reported_model": identity.model,
            "reported_firmware": identity.firmware,
            "recognized_model": normalized_model,
            "recognized_firmware": normalize_firmware(
                identity.firmware,
                normalized_model,
            ),
            "availability_reason": self._availability_reason(),
        }

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
