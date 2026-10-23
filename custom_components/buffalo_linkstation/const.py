"""Constants for the Buffalo LinkStation integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

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
CONF_PROTOCOL = "protocol"

PROTOCOL_JSON_RPC = "json_rpc"
PROTOCOL_LEGACY_CGI = "legacy_cgi"

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

# Alternate product strings and full version displays confirmed by the two
# sanitized hardware captures and the matching Buffalo Web UI implementation.
# Keep these aliases explicit so an unverified model or build is never enabled
# merely because its name starts with a verified model number.
MODEL_ALIASES = {
    "LS220D(SANJO)": "LS220D",
    "LS420D(LS-WM)": "LS420D",
    "LS-WVL(KOUGYOKU)": "LS-WVL",
}
FIRMWARE_ALIASES = {
    ("LS220D", "1.86-0.07"): "1.86",
    ("LS420D", "1.88-0.03"): "1.88",
}

# Feature keys observed in getNasFeature responses.
FEATURE_REBOOT = "SUPPORT_REBOOT_FROMWEB"
FEATURE_SHUTDOWN = "SUPPORT_SHUTDOWN_FROMWEB"

REDACTED = "**REDACTED**"


def normalize_model(model: str | None) -> str | None:
    """Normalize a confirmed Buffalo model spelling without widening support."""
    if model is None:
        return None
    normalized = "".join(model.split()).upper()
    if not normalized:
        return None
    return MODEL_ALIASES.get(normalized, normalized)


def normalize_firmware(firmware: str | None, model: str | None = None) -> str | None:
    """Normalize confirmed major/build formatting for a specific model."""
    if firmware is None:
        return None
    normalized = "".join(firmware.split())
    if not normalized:
        return None
    normalized_model = normalize_model(model)
    return FIRMWARE_ALIASES.get((normalized_model, normalized), normalized)


def is_verified_shutdown(model: str | None, firmware: str | None) -> bool:
    """Return whether shutdown was verified for this exact model/firmware pair."""
    normalized_model = normalize_model(model)
    normalized_firmware = normalize_firmware(firmware, normalized_model)
    if normalized_model is None or normalized_firmware is None:
        return False
    versions = VERIFIED_SHUTDOWN_FIRMWARE.get(normalized_model)
    return versions is not None and normalized_firmware in versions


def is_shutdown_enabled(
    options: Mapping[str, Any], model: str | None, firmware: str | None
) -> bool:
    """Return the explicit shutdown setting or its compatibility default."""
    if CONF_ENABLE_SHUTDOWN in options:
        return bool(options[CONF_ENABLE_SHUTDOWN])
    return is_verified_shutdown(model, firmware)


def is_reboot_enabled(options: Mapping[str, Any]) -> bool:
    """Return the explicit reboot setting, which defaults to disabled."""
    return bool(options.get(CONF_ENABLE_REBOOT, False))
