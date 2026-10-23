"""Protocol-neutral Buffalo LinkStation client and identity models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class BuffaloIdentity:
    """Stable and display identity derived from confirmed API data."""

    hostname: str | None = None
    model: str | None = None
    firmware: str | None = None
    serial_number: str | None = None


class BuffaloLinkStationClient(Protocol):
    """Transport-independent operations consumed by the integration."""

    host: str

    @property
    def endpoint(self) -> str:
        """Return the configured local API endpoint."""

    async def prepare_update(self) -> None:
        """Ensure authentication is ready before one polling update."""

    async def get_system_info(self) -> dict[str, Any]:
        """Return normalized identity and optional system information."""

    async def get_features(self) -> dict[str, Any]:
        """Return advertised feature metadata."""

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return normalized disk and array records."""

    async def shutdown(self) -> Any:
        """Request an explicitly user-initiated shutdown."""

    async def reboot(self) -> Any:
        """Request an explicitly user-initiated reboot."""
