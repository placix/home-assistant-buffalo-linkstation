"""Constants for the Buffalo LinkStation integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "buffalo_linkstation"
MANUFACTURER = "Buffalo"

PLATFORMS: tuple[str, ...] = (
    "sensor",
    "binary_sensor",
    "button",
)

CONF_UPDATE_INTERVAL = "update_interval"
CONF_TIMEOUT = "timeout"
CONF_ENABLE_SHUTDOWN = "enable_shutdown"
CONF_ENABLE_REBOOT = "enable_reboot"
CONF_DEVICE_ID = "device_id"

DEFAULT_UPDATE_INTERVAL = 60
DEFAULT_TIMEOUT = 20
MIN_UPDATE_INTERVAL = 15
MAX_UPDATE_INTERVAL = 3600
MIN_TIMEOUT = 3
MAX_TIMEOUT = 60

DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_UPDATE_INTERVAL)

# Model/firmware combinations on which System.shutdown was verified on hardware.
# The feature flag is intentionally not involved: LS220D 1.86 reports 0 while the
# authenticated JSON-RPC method works.
VERIFIED_SHUTDOWN_FIRMWARE = {
    "LS220D": frozenset({"1.86"}),
    "LS420D": frozenset({"1.88"}),
}

# Feature keys observed in getNasFeature responses.
FEATURE_REBOOT = "SUPPORT_REBOOT_FROMWEB"
FEATURE_SHUTDOWN = "SUPPORT_SHUTDOWN_FROMWEB"

REDACTED = "**REDACTED**"


def is_verified_shutdown(model: str | None, firmware: str | None) -> bool:
    """Return whether shutdown was verified for this exact model/firmware pair."""
    if model is None or firmware is None:
        return False
    versions = VERIFIED_SHUTDOWN_FIRMWARE.get(model.strip().upper())
    return versions is not None and firmware.strip() in versions
