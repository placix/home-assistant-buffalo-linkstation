"""Capture and sanitize read-only Buffalo LinkStation JSON-RPC results."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import ipaddress
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from aiohttp import ClientSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from custom_components.buffalo_linkstation.api import (  # noqa: E402
    BuffaloJsonRpcError,
    BuffaloLinkStationApi,
    BuffaloLinkStationError,
    extract_identity,
)

CAPTURE_FORMAT: Final = 1
CAPTURE_DIRECTORY: Final = PROJECT_ROOT / "reference" / "captures"
READ_METHODS: Final = (
    "get_portal_info_authneed",
    "getNasFeature",
    "device.getlist",
)
MODEL_FIRMWARE: Final = {"LS220D": "1.86", "LS420D": "1.88"}
OUTPUT_NAMES: Final = {
    "LS220D": "ls220d_1_86.json",
    "LS420D": "ls420d_1_88.json",
}
DIAGNOSTIC_OUTPUT_NAMES: Final = {
    "LS220D": "ls220d_1_86_diagnostic.json",
    "LS420D": "ls420d_1_88_diagnostic.json",
}
COMPLETED_OUTPUT_NAMES: Final = {
    "LS220D": "ls220d_1_86_completed.json",
    "LS420D": "ls420d_1_88_completed.json",
}
REDACTED: Final = "<redacted>"

_SENSITIVE_KEY_PARTS: Final = (
    "credential",
    "description",
    "dns",
    "domain",
    "email",
    "ethaddr",
    "gateway",
    "hostname",
    "ipaddr",
    "ipaddress",
    "loginname",
    "macaddr",
    "message",
    "nasname",
    "owner",
    "parameter",
    "password",
    "passwd",
    "serial",
    "sessionid",
    "targetobject",
    "text",
    "username",
    "workgroup",
)
_SENSITIVE_EXACT_KEYS: Final = frozenset({"sid", "uid", "user"})
_IPV4_RE: Final = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
_MAC_RE: Final = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])"
)
_EMAIL_RE: Final = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_IPV6_CANDIDATE_RE: Final = re.compile(
    r"(?i)(?<![0-9a-f:])\[?[0-9a-f:]*:[0-9a-f:]+\]?(?![0-9a-f:])"
)
_URL_RE: Final = re.compile(r"(?i)https?://[^\s]+")


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """Paths and completion state for one operator-requested capture."""

    capture_path: Path
    diagnostic_path: Path | None
    complete: bool


def _normalized_key(key: Any) -> str:
    """Normalize a response key for privacy classification."""
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def _sanitize_string(value: str, entered_host: str) -> str:
    """Remove common identifiers even when they occur under an unknown key."""
    sanitized = value
    if entered_host:
        sanitized = re.sub(
            re.escape(entered_host),
            REDACTED,
            sanitized,
            flags=re.IGNORECASE,
        )
    sanitized = _MAC_RE.sub(REDACTED, sanitized)
    sanitized = _IPV4_RE.sub(REDACTED, sanitized)
    sanitized = _EMAIL_RE.sub(REDACTED, sanitized)
    sanitized = _URL_RE.sub(REDACTED, sanitized)

    def redact_ipv6(match: re.Match[str]) -> str:
        candidate = match.group(0).strip("[]")
        try:
            return (
                REDACTED if ipaddress.ip_address(candidate).version == 6 else candidate
            )
        except ValueError:
            return match.group(0)

    return _IPV6_CANDIDATE_RE.sub(redact_ipv6, sanitized)


def sanitize(value: Any, *, entered_host: str = "", key: Any = None) -> Any:
    """Return a JSON-compatible copy with identifying values redacted."""
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize(
                item_value,
                entered_host=entered_host,
                key=item_key,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [sanitize(item, entered_host=entered_host) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value, entered_host)
    # Keep numbers unchanged so raw storage and progress values survive.
    return value


def build_capture(
    model: str,
    portal: Mapping[str, Any],
    features: Mapping[str, Any],
    devices: list[dict[str, Any]],
    *,
    entered_host: str,
) -> dict[str, Any]:
    """Build the sanitized, model-specific capture document."""
    methods = {
        "get_portal_info_authneed": sanitize(portal, entered_host=entered_host),
        "getNasFeature": sanitize(features, entered_host=entered_host),
        "device.getlist": {"devices": sanitize(devices, entered_host=entered_host)},
    }
    return _build_capture_from_methods(model, methods, complete=True)


def _build_capture_from_methods(
    model: str,
    methods: Mapping[str, Any],
    *,
    complete: bool,
) -> dict[str, Any]:
    """Build a capture whose completion state cannot be mistaken."""
    return {
        "capture_format": CAPTURE_FORMAT,
        "model": model,
        "firmware": MODEL_FIRMWARE[model],
        "source": "hardware_read_only",
        "complete": complete,
        "methods": dict(methods),
    }


async def _collect_read_methods(
    api: BuffaloLinkStationApi,
    entered_host: str,
    reporter: Callable[[str], None] = print,
    *,
    methods: Sequence[str] = READ_METHODS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Call every allowlisted method and retain independent outcomes."""
    available_calls: dict[str, Callable[[], Awaitable[Any]]] = {
        "get_portal_info_authneed": api.get_system_info,
        "getNasFeature": api.get_features,
        "device.getlist": api.get_devices,
    }
    if not set(methods).issubset(available_calls):
        raise ValueError("Requested capture method is not in the read-only allowlist")
    successful: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []

    for method in methods:
        call = available_calls[method]
        reporter(f"[INFO] Calling {method}...")
        try:
            result = await call()
        except BuffaloJsonRpcError as err:
            message = _sanitize_string(err.message, entered_host)
            failures.append(
                {
                    "method": method,
                    "error_code": err.code,
                    "error_type": "json_rpc",
                    "message": message,
                }
            )
            code = err.code if err.code is not None else "unknown"
            reporter(f"[ERROR] JSON-RPC {code}: {message}")
        except BuffaloLinkStationError as err:
            message = _sanitize_string(str(err), entered_host)
            failures.append(
                {
                    "method": method,
                    "error_code": None,
                    "error_type": type(err).__name__,
                    "message": message,
                }
            )
            reporter(f"[ERROR] {type(err).__name__}: {message}")
        else:
            if method == "device.getlist":
                result = {"devices": result}
            successful[method] = sanitize(result, entered_host=entered_host)
            reporter("[OK] Request successful.")

    return successful, failures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture three read-only Buffalo LinkStation API results."
    )
    parser.add_argument("--model", choices=tuple(MODEL_FIRMWARE))
    parser.add_argument(
        "--retry-features",
        action="store_true",
        help=(
            "Retry only getNasFeature and merge it into a new completed file; "
            "the existing partial capture is preserved."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Request timeout in seconds; credentials are never accepted as arguments.",
    )
    return parser.parse_args()


async def _capture(
    model: str,
    host: str,
    username: str,
    password: str,
    request_timeout: float | None,
    *,
    reporter: Callable[[str], None] = print,
) -> CaptureOutcome:
    """Perform the explicitly requested read-only calls and save sanitized data."""
    output_path = CAPTURE_DIRECTORY / OUTPUT_NAMES[model]
    diagnostic_path = CAPTURE_DIRECTORY / DIAGNOSTIC_OUTPUT_NAMES[model]
    existing_path = next(
        (path for path in (output_path, diagnostic_path) if path.exists()),
        None,
    )
    if existing_path is not None:
        raise FileExistsError(
            f"Capture output already exists: {existing_path}. "
            "Move it aside before recapturing."
        )

    async with ClientSession() as session:
        kwargs = {"timeout": request_timeout} if request_timeout is not None else {}
        api = BuffaloLinkStationApi(session, host, username, password, **kwargs)
        reporter("[INFO] Authenticating...")
        await api.login()
        reporter("[OK] Authentication successful.")
        successful, failures = await _collect_read_methods(api, host, reporter)

    portal = successful.get("get_portal_info_authneed")
    if isinstance(portal, Mapping):
        identity = extract_identity(portal, "")
        if identity.model and identity.model.strip().upper() != model:
            raise ValueError("The NAS-reported model does not match the selected model")
        if identity.firmware and identity.firmware.strip() != MODEL_FIRMWARE[model]:
            raise ValueError(
                "The NAS-reported firmware does not match the selected firmware"
            )

    complete = not failures and set(successful) == set(READ_METHODS)
    capture = _build_capture_from_methods(model, successful, complete=complete)
    CAPTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(capture, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    saved_diagnostic_path: Path | None = None
    if failures:
        diagnostic = {
            "diagnostic_format": 1,
            "model": model,
            "firmware": MODEL_FIRMWARE[model],
            "capture_status": "partial",
            "failed_methods": failures,
        }
        diagnostic_path.write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        saved_diagnostic_path = diagnostic_path

    return CaptureOutcome(output_path, saved_diagnostic_path, complete)


async def _retry_feature_capture(
    model: str,
    host: str,
    request_timeout: float | None,
    *,
    reporter: Callable[[str], None] = print,
) -> Path | None:
    """Retry only getNasFeature and preserve the original partial capture."""
    output_path = CAPTURE_DIRECTORY / COMPLETED_OUTPUT_NAMES[model]
    if output_path.exists():
        raise FileExistsError(
            f"Completed capture already exists: {output_path}. Move it aside first."
        )
    methods = _load_partial_methods(model)

    async with ClientSession() as session:
        kwargs = {"timeout": request_timeout} if request_timeout is not None else {}
        api = BuffaloLinkStationApi(session, host, "", "", **kwargs)
        successful, failures = await _collect_read_methods(
            api,
            host,
            reporter,
            methods=("getNasFeature",),
        )
    if failures:
        return None

    methods.update(successful)
    if not set(READ_METHODS).issubset(methods):
        raise ValueError("The refreshed capture is still missing a required method")
    completed = _build_capture_from_methods(model, methods, complete=True)
    output_path.write_text(
        json.dumps(completed, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def _load_partial_methods(model: str) -> dict[str, Any]:
    """Validate a sanitized partial capture and return a mutable method copy."""
    source_path = CAPTURE_DIRECTORY / OUTPUT_NAMES[model]
    if not source_path.exists():
        raise FileNotFoundError(f"Partial capture does not exist: {source_path}")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(source, Mapping):
        raise TypeError("The existing capture is not a JSON object")
    if source.get("model") != model or source.get("firmware") != MODEL_FIRMWARE[model]:
        raise ValueError("The existing capture model or firmware does not match")
    if source.get("complete") is not False:
        raise ValueError("Feature retry requires an explicitly incomplete capture")
    if sanitize(source) != source:
        raise ValueError("The existing capture is not fully sanitized")
    methods = source.get("methods")
    if not isinstance(methods, Mapping):
        raise TypeError("The existing capture does not contain a methods object")
    missing = set(READ_METHODS) - set(methods)
    if missing != {"getNasFeature"}:
        raise ValueError(
            "Feature retry requires getNasFeature to be the sole missing method"
        )
    return dict(methods)


def _run_feature_retry(model: str, request_timeout: float | None) -> int:
    """Prompt for and execute the explicit one-method refresh mode."""
    host = input("NAS hostname or IP address: ").strip()
    if not host:
        print("Host is required.", file=sys.stderr)
        return 2
    source_path = CAPTURE_DIRECTORY / OUTPUT_NAMES[model]
    output_path = CAPTURE_DIRECTORY / COMPLETED_OUTPUT_NAMES[model]
    print("The tool will call only getNasFeature without a session ID.")
    print(f"Existing partial capture will remain unchanged: {source_path}")
    print(f"A successful merged capture will be written to: {output_path}")
    if input("Continue? [y/N]: ").strip().casefold() not in {"y", "yes"}:
        print("Cancelled; no NAS request was sent.")
        return 0
    logging.basicConfig(level=logging.WARNING)
    try:
        completed_path = asyncio.run(
            _retry_feature_capture(model, host, request_timeout)
        )
    except (
        BuffaloLinkStationError,
        FileNotFoundError,
        FileExistsError,
        TypeError,
        json.JSONDecodeError,
        ValueError,
    ) as err:
        print(f"Feature retry failed: {err}", file=sys.stderr)
        return 1
    if completed_path is None:
        print(
            "Feature retry failed; the original partial capture was preserved.",
            file=sys.stderr,
        )
        return 1
    print(f"Complete sanitized capture saved to {completed_path}")
    print("The original partial capture and diagnostic remain unchanged.")
    return 0


def _run_full_capture(model: str, request_timeout: float | None) -> int:
    """Prompt for credentials and execute the full read-only capture."""
    host = input("NAS hostname or IP address: ").strip()
    username = input("NAS username: ").strip()
    password = getpass.getpass("NAS password: ")
    if not host or not username or not password:
        print("Host, username, and password are required.", file=sys.stderr)
        return 2

    print("The tool will call only these read-only methods:")
    for method in READ_METHODS:
        print(f"  - {method}")
    if input("Continue? [y/N]: ").strip().casefold() not in {"y", "yes"}:
        print("Cancelled; no NAS request was sent.")
        return 0

    logging.basicConfig(level=logging.WARNING)
    try:
        outcome = asyncio.run(
            _capture(model, host, username, password, request_timeout)
        )
    except (BuffaloLinkStationError, FileExistsError, ValueError) as err:
        print(f"Capture failed: {err}", file=sys.stderr)
        return 1

    if not outcome.complete:
        print(f"Sanitized partial capture saved to {outcome.capture_path}")
        print(
            f"Partial capture: failure details saved to {outcome.diagnostic_path}",
            file=sys.stderr,
        )
        print("The capture is incomplete and must not be treated as validated data.")
        return 1
    print(f"Complete sanitized capture saved to {outcome.capture_path}")
    print("Review the file manually before sharing it. It remains ignored by Git.")
    return 0


def main() -> int:
    """Prompt for private connection details and run only after confirmation."""
    args = _parse_args()
    model = args.model or input("Model (LS220D or LS420D): ").strip().upper()
    if model not in MODEL_FIRMWARE:
        print("Unsupported model selection.", file=sys.stderr)
        return 2
    if args.retry_features:
        return _run_feature_retry(model, args.timeout)
    return _run_full_capture(model, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
