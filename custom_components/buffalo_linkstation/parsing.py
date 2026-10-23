"""Pure response parsing for Buffalo LinkStation data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class LinkStationData(Protocol):
    """Fields required by response parsers without Home Assistant imports."""

    system_info: dict[str, Any]
    devices: tuple[dict[str, Any], ...]
    identity: Any


_STATUS_PRIORITY = (
    "broken",
    "error",
    "failed",
    "rebuild",
    "degrade",
    "degraded",
    "reshape",
    "resync",
    "repair",
    "removed",
    "formatting",
    "checking",
    "disconnected",
    "unformatted",
    "fine",
    "ok",
)


def find_value(data: Any, *keys: str) -> Any:
    """Return the first value matching a known field name recursively."""
    wanted = {key.casefold() for key in keys}
    if isinstance(data, Mapping):
        for key, value in data.items():
            if str(key).casefold() in wanted and value not in (None, ""):
                return value
        for value in data.values():
            found = find_value(value, *keys)
            if found is not None:
                return found
    elif isinstance(data, list | tuple):
        for value in data:
            found = find_value(value, *keys)
            if found is not None:
                return found
    return None


def feature_enabled(features: Mapping[str, Any], name: str) -> bool:
    """Interpret only explicit common true representations."""
    value = find_value(features, name)
    return (
        value is True
        or value == 1
        or (
            isinstance(value, str)
            and value.strip().casefold() in {"1", "true", "yes", "on"}
        )
    )


def firmware(data: LinkStationData) -> str | None:
    """Return the identified firmware version."""
    return data.identity.firmware


def cpu_status(data: LinkStationData) -> str | None:
    """Return the portal CPU status when present."""
    value = data.system_info.get("cpu_status")
    return str(value) if value not in (None, "") else None


def _messages(data: LinkStationData, key: str) -> str | None:
    values = data.system_info.get(key)
    if not isinstance(values, list) or not values:
        return None
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            item = find_value(value, "description", "message", "code")
            if item not in (None, ""):
                result.append(str(item))
        elif value not in (None, ""):
            result.append(str(value))
    return ", ".join(result) or None


def system_errors(data: LinkStationData) -> str | None:
    """Summarize portal system errors."""
    return _messages(data, "errors")


def system_messages(data: LinkStationData) -> str | None:
    """Summarize portal information messages."""
    return _messages(data, "informations")


def storage_totals(data: LinkStationData) -> tuple[float | int, float | int] | None:
    """Return valid used and total values in their transport-specific unit."""
    storage = data.system_info.get("storage")
    if isinstance(storage, Mapping):
        used = storage.get("used")
        total = storage.get("total")
        if (
            isinstance(used, int | float)
            and not isinstance(used, bool)
            and isinstance(total, int | float)
            and not isinstance(total, bool)
            and used >= 0
            and total >= 0
        ):
            return used, total
    disks = data.system_info.get("disks")
    if not isinstance(disks, list):
        return None
    used = total = 0.0
    found = False
    for disk in disks:
        if not isinstance(disk, Mapping):
            continue
        try:
            disk_used = float(disk["used_size"])
            disk_total = float(disk["total_size"])
        except (KeyError, TypeError, ValueError):
            continue
        if disk_total < 0 or disk_used < 0:
            continue
        used += disk_used
        total += disk_total
        found = True
    return (used, total) if found else None


def used_storage(data: LinkStationData) -> float | None:
    """Return used storage in the unit reported by `storage_display_unit`."""
    totals = storage_totals(data)
    if storage_source_unit(data) == "vendor_reported_kb":
        return totals[0] if totals else None
    return round(totals[0] / 1024 / 1024, 1) if totals else None


def total_storage(data: LinkStationData) -> float | None:
    """Return total storage in the unit reported by `storage_display_unit`."""
    totals = storage_totals(data)
    if storage_source_unit(data) == "vendor_reported_kb":
        return totals[1] if totals else None
    return round(totals[1] / 1024 / 1024, 1) if totals else None


def storage_source_unit(data: LinkStationData) -> str:
    """Return the normalized source-unit identifier."""
    storage = data.system_info.get("storage")
    if isinstance(storage, Mapping) and storage.get("unit") == "vendor_reported_kb":
        return "vendor_reported_kb"
    return "kib"


def storage_display_unit(data: LinkStationData) -> str:
    """Return a display unit without guessing the legacy byte basis."""
    if storage_source_unit(data) == "vendor_reported_kb":
        return "KB (reported)"
    return "GiB"


def utilization(data: LinkStationData) -> float | None:
    """Calculate utilization from the same raw portal totals."""
    storage = data.system_info.get("storage")
    if isinstance(storage, Mapping) and storage.get("utilization") not in (None, ""):
        try:
            value = float(storage["utilization"])
        except (TypeError, ValueError):
            pass
        else:
            return round(value, 1) if value >= 0 else None
    totals = storage_totals(data)
    if totals is None or totals[1] <= 0:
        return None
    return round(totals[0] / totals[1] * 100, 1)


def _device_status(
    data: LinkStationData,
    device_type: str,
    *,
    exclude_interface: str | None = None,
) -> str | None:
    statuses: list[str] = []
    for device in data.devices:
        if device.get("device_type") != device_type:
            continue
        interface = device.get("interface")
        if (
            exclude_interface is not None
            and isinstance(interface, str)
            and interface.casefold() == exclude_interface
        ):
            continue
        value = device.get("status")
        if isinstance(value, list):
            statuses.extend(str(item) for item in value)
        elif value not in (None, ""):
            statuses.append(str(value))
    normalized = {status.casefold() for status in statuses}
    prioritized = next(
        (status for status in _STATUS_PRIORITY if status in normalized), None
    )
    return prioritized or (statuses[0] if statuses else None)


def disk_status(data: LinkStationData) -> str | None:
    """Return internal-disk status, excluding USB slots and attached media."""
    return _device_status(data, "disk", exclude_interface="usb")


def raid_status(data: LinkStationData) -> str | None:
    """Return the highest-priority array status."""
    return _device_status(data, "array")


def raid_mode(data: LinkStationData) -> str | None:
    """Return the configured RAID mode when supplied by the transport."""
    modes = {
        str(device["raid_level"])
        for device in data.devices
        if device.get("device_type") == "array"
        and device.get("raid_level") not in (None, "")
    }
    return ", ".join(sorted(modes)) or None


def disk_details(data: LinkStationData) -> list[dict[str, Any]]:
    """Return non-sensitive normalized internal-disk membership details."""
    return [
        {
            key: device.get(key)
            for key in ("name", "status", "array", "member_state")
            if device.get(key) not in (None, "")
        }
        for device in data.devices
        if device.get("device_type") == "disk"
        and str(device.get("interface", "")).casefold() != "usb"
    ]


def raid_details(data: LinkStationData) -> list[dict[str, Any]]:
    """Return non-sensitive normalized array configuration details."""
    return [
        {
            key: device.get(key)
            for key in ("name", "status", "raid_level", "members")
            if device.get(key) not in (None, "", ())
        }
        for device in data.devices
        if device.get("device_type") == "array"
    ]


def system_problem(data: LinkStationData) -> bool | None:
    """Return whether the portal reports system errors."""
    errors = data.system_info.get("errors")
    return bool(errors) if isinstance(errors, list) else None


def raid_problem(data: LinkStationData) -> bool | None:
    """Return whether any array has a confirmed failure/degraded state."""
    arrays = [device for device in data.devices if device.get("device_type") == "array"]
    if not arrays:
        return None
    problem_states = {
        "broken",
        "degrade",
        "degraded",
        "error",
        "failed",
        "removed",
        "disconnected",
        "unformatted",
    }
    for device in arrays:
        value = device.get("status")
        statuses = value if isinstance(value, list) else [value]
        if any(str(status).casefold() in problem_states for status in statuses):
            return True
    return False
