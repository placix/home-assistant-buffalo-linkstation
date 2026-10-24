"""Create a minimal, privacy-first diagnostic document from a browser HAR."""

from __future__ import annotations

import argparse
import base64
import binascii
import ipaddress
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit

FORMAT_VERSION: Final = 2
DEFAULT_OUTPUT_NAME: Final = "buffalo-diagnostic-sanitized.json"
MAX_INPUT_BYTES: Final = 100 * 1024 * 1024
MAX_BODY_BYTES: Final = 2 * 1024 * 1024
MAX_FEATURE_FIELD_NAMES: Final = 32
MAX_LEGACY_FEATURE_SCHEMA_VARIANTS: Final = 8
MAX_UNKNOWN_PROTOCOL_SAMPLES: Final = 5
REDACTED_FIELD: Final = "<redacted-field>"

JSON_RPC_PATH: Final = "/nasapi/"
LEGACY_CGI_PATH: Final = "/dynamic.pl"

CONFIRMED_JSON_RPC_READ_METHODS: Final = frozenset(
    {
        "Backup.listSources",
        "Backup.listTargets",
        "Job.detail",
        "Job.listJobs",
        "MailNotification.detail",
        "device.getlist",
        "failover.get_config",
        "getNasFeature",
        "get_portal_info",
        "get_portal_info_authneed",
        "get_service_status",
        "isconnected_internet",
        "iscsi.detail_service",
        "list_folders",
        "network.listinterfacesettings",
        "raid.detail_raidmode_maintenance",
        "replication.listJobs",
        "share.getlist",
        "smb.detail_service",
        "ups.get_config",
        "user.getlist",
        "User.detail",
    }
)
CONFIRMED_JSON_RPC_AUTH_METHODS: Final = frozenset(
    {
        "Auth.get_uid",
        "Auth.login",
        "Auth.logout",
        "user_.get_username",
        "User.get_username",
    }
)
CONFIRMED_JSON_RPC_SENSITIVE_METHODS: Final = frozenset(
    {
        "Backup.updateFolders",
        "iscsi.edit_service",
        "MailNotification.edit",
        "raid.edit_raidmode_maintenance",
        "service_control",
        "sound.find_location_device",
        "system.restore_db",
        "System.reboot",
        "System.shutdown",
    }
)
CONFIRMED_JSON_RPC_METHODS: Final = (
    CONFIRMED_JSON_RPC_READ_METHODS
    | CONFIRMED_JSON_RPC_AUTH_METHODS
    | CONFIRMED_JSON_RPC_SENSITIVE_METHODS
)
KNOWN_CGI_ACTIONS: Final = frozenset(
    {
        "verifyLogin",
        "validateSession",
        "getFeatures",
        "getDiskAllList",
        "getArrayList",
        "getArrayMaintenance",
        "getArrayMiscInfo",
        "getUPSSettings",
        "getOperationStatus",
        "shutRestartLS",
    }
)
CGI_AUTH_OPERATIONS: Final = frozenset({"verifyLogin", "validateSession"})
CGI_SENSITIVE_OPERATIONS: Final = frozenset({"shutRestartLS"})
SAFE_OPERATION_NAMES: Final = (
    CONFIRMED_JSON_RPC_METHODS | KNOWN_CGI_ACTIONS | frozenset({"shutdown", "reboot"})
)
SENSITIVE_OPERATION_PARTS: Final = (
    "add",
    "create",
    "delete",
    "firmware",
    "format",
    "permission",
    "reboot",
    "remove",
    "restart",
    "shutdown",
    "update",
    "user",
    "write",
)
SENSITIVE_KEY_PARTS: Final = (
    "authorization",
    "cookie",
    "credential",
    "email",
    "filename",
    "hostname",
    "login",
    "mac",
    "password",
    "path",
    "serial",
    "session",
    "share",
    "sid",
    "token",
    "user",
)
SAFE_STATUS_KEYS: Final = frozenset(
    {
        "arraymode",
        "arraystatus",
        "code",
        "devicetype",
        "errorcode",
        "health",
        "mode",
        "raidlevel",
        "state",
        "status",
        "success",
    }
)
SAFE_STATUS_VALUES: Final = frozenset(
    {
        "active",
        "array",
        "degraded",
        "disabled",
        "disconnect",
        "enabled",
        "error",
        "failed",
        "healthy",
        "inactive",
        "normal",
        "off",
        "ok",
        "on",
        "raid0",
        "raid1",
        "raid5",
        "raid6",
        "rebuilding",
        "sata",
        "spare",
        "usb",
        "warning",
    }
)
SAFE_MIME_TYPES: Final = frozenset(
    {
        "application/json",
        "application/x-www-form-urlencoded",
        "text/json",
        "text/plain",
    }
)
REMOVAL_SUMMARY: Final = (
    "HAR creator, browser, page, timing, cache, and connection metadata",
    "full URLs, origins, hostnames, and network addresses",
    "all request and response header values",
    "all cookies, authorization values, credentials, and session identifiers",
    "request and response free-form text values",
    "device-specific numeric measurements and numeric request values",
    "filenames, share names, paths, serial numbers, MAC addresses, and email addresses",
    "authentication response bodies and bodies for sensitive operations",
)
STATIC_EXTENSIONS: Final = frozenset(
    {
        ".avif",
        ".bmp",
        ".css",
        ".eot",
        ".gif",
        ".htm",
        ".html",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".map",
        ".png",
        ".svg",
        ".ttf",
        ".webp",
        ".woff",
        ".woff2",
        ".xml",
    }
)
STATIC_MIME_PREFIXES: Final = ("font/", "image/")
STATIC_MIME_TYPES: Final = frozenset(
    {
        "application/javascript",
        "application/x-javascript",
        "text/css",
        "text/html",
        "text/javascript",
    }
)

_SAFE_NAME_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")
_SAFE_OPERATION_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_SAFE_USER_VALUE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,63}$")
_MAC_RE: Final = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])"
)
_IPV4_RE: Final = re.compile(
    r"(?<![0-9.])(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}(?![0-9.])"
)
_EMAIL_RE: Final = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_DOMAIN_RE: Final = re.compile(
    r"(?i)\b[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"\.(?:com|de|home|internal|invalid|lan|local|net|org)\b"
)
_FILE_EXTENSION_RE: Final = re.compile(
    r"(?i)\.(?:bak|csv|docx?|gif|jpe?g|key|log|mp[34]|pdf|pem|png|pptx?|sql|txt|xlsx?|zip)$"
)


class HarSanitizationError(ValueError):
    """Raised when an input cannot be safely interpreted as a HAR document."""


@dataclass(slots=True)
class _AnalysisState:
    """Accumulate sanitized entries and broad skip statistics."""

    origins: dict[tuple[str, str, int | None], str] = field(default_factory=dict)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)
    detected_protocol_entries: Counter[str] = field(default_factory=Counter)
    unknown_candidates: list[tuple[int, Mapping[str, Any], Mapping[str, Any], str]] = (
        field(default_factory=list)
    )


def _normalized_key(value: object) -> str:
    """Return a comparison-only representation of a field name."""
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _looks_identifying(value: str) -> bool:
    """Return whether text resembles an identifier that must not be retained."""
    if (
        not value
        or _MAC_RE.search(value)
        or _IPV4_RE.search(value)
        or _EMAIL_RE.search(value)
        or _DOMAIN_RE.search(value)
    ):
        return True
    if any(character in value for character in ("/", "\\", "@")):
        return True
    if _FILE_EXTENSION_RE.search(value):
        return True
    candidate = value.strip("[]")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


def _safe_name(value: object) -> str | None:
    """Return a non-identifying structural name, or None when uncertain."""
    name = str(value)
    if not _SAFE_NAME_RE.fullmatch(name) or _looks_identifying(name):
        return None
    return name


def _safe_user_value(value: str | None) -> str | None:
    """Conservatively retain optional model or firmware supplied by the user."""
    if value is None:
        return None
    stripped = value.strip()
    if not _SAFE_USER_VALUE_RE.fullmatch(stripped) or _looks_identifying(stripped):
        return "<redacted>"
    return stripped


def _value_type(value: Any) -> str:
    """Return a JSON-oriented type label without copying a value."""
    if value is None:
        value_type = "null"
    elif isinstance(value, bool):
        value_type = "boolean"
    elif isinstance(value, int):
        value_type = "integer"
    elif isinstance(value, float):
        value_type = "number"
    elif isinstance(value, Mapping):
        value_type = "object"
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        value_type = "array"
    elif isinstance(value, str):
        value_type = "string"
    else:
        value_type = "unknown"
    return value_type


def _is_sensitive_key(key: object) -> bool:
    """Return whether a field name identifies authentication or personal data."""
    normalized = _normalized_key(key)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _safe_scalar(value: Any, key: object | None) -> dict[str, Any]:
    """Describe a scalar, retaining only tightly allowlisted status values."""
    result: dict[str, Any] = {"type": _value_type(value)}
    if key is None or _is_sensitive_key(key):
        return result
    normalized = _normalized_key(key)
    keep_boolean = isinstance(value, bool) and normalized in SAFE_STATUS_KEYS
    if keep_boolean:
        result["value"] = value
    elif isinstance(value, str):
        folded = value.casefold().strip()
        if normalized in SAFE_STATUS_KEYS and folded in SAFE_STATUS_VALUES:
            result["value"] = folded
    return result


def _sensitive_structure(value: Any, key: object | None) -> dict[str, Any] | None:
    """Describe a sensitive field without traversing container contents."""
    if key is None or not _is_sensitive_key(key):
        return None
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "field_count": len(value),
            "contents": "omitted_sensitive_container",
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return {
            "type": "array",
            "length": len(value),
            "contents": "omitted_sensitive_container",
        }
    return _safe_scalar(value, key)


def describe_structure(
    value: Any,
    *,
    key: object | None = None,
    depth: int = 0,
) -> dict[str, Any]:
    """Describe JSON structure while discarding unclassified values."""
    if depth >= 16:
        return {"type": "omitted", "reason": "maximum_depth"}
    sensitive = _sensitive_structure(value, key)
    if sensitive is not None:
        return sensitive
    if isinstance(value, Mapping):
        fields: dict[str, Any] = {}
        redacted_fields = 0
        for raw_key, item in value.items():
            safe_key = _safe_name(raw_key)
            if safe_key is None:
                redacted_fields += 1
                continue
            fields[safe_key] = describe_structure(
                item,
                key=safe_key,
                depth=depth + 1,
            )
        result: dict[str, Any] = {"type": "object", "fields": fields}
        if redacted_fields:
            result["redacted_field_count"] = redacted_fields
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return {
            "type": "array",
            "length": len(value),
            "items": [
                describe_structure(item, key=key, depth=depth + 1) for item in value
            ],
        }
    return _safe_scalar(value, key)


def _parameter_summary(items: Sequence[Any]) -> list[dict[str, Any]]:
    """Return parameter names and value types, never parameter values."""
    parameters: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_name = item.get("name")
        name = _safe_name(raw_name) if raw_name is not None else None
        value = item.get("value")
        entry: dict[str, Any] = {
            "name": name or REDACTED_FIELD,
            "type": _value_type(value),
        }
        if raw_name is not None and _is_sensitive_key(raw_name):
            entry["sensitive"] = True
        parameters.append(entry)
    return parameters


def _parse_form_pairs(text: str) -> list[tuple[str, str]]:
    """Parse a bounded number of form pairs or omit malformed input."""
    if len(text.encode(errors="ignore")) > MAX_BODY_BYTES:
        return []
    try:
        return parse_qsl(text, max_num_fields=10_000)
    except ValueError:
        return []


def _form_parameters(post_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract form parameter names/types from HAR params or encoded text."""
    params = post_data.get("params")
    if isinstance(params, list):
        return [dict(item) for item in params if isinstance(item, Mapping)]
    text = post_data.get("text")
    if not isinstance(text, str):
        return []
    return [{"name": key, "value": value} for key, value in _parse_form_pairs(text)]


def _query_parameters(request: Mapping[str, Any], query: str) -> list[dict[str, Any]]:
    """Extract query parameter names/types without retaining values."""
    values = request.get("queryString")
    if isinstance(values, list) and values:
        return [dict(item) for item in values if isinstance(item, Mapping)]
    return [{"name": key, "value": value} for key, value in _parse_form_pairs(query)]


def _post_json(post_data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Parse a bounded JSON request body without surfacing parse details."""
    text = post_data.get("text")
    if not isinstance(text, str) or len(text.encode(errors="ignore")) > MAX_BODY_BYTES:
        return None
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, Mapping) else None


def _safe_operation(value: Any) -> str | None:
    """Return only a source-confirmed API operation name."""
    if not isinstance(value, str):
        return None
    if not _SAFE_OPERATION_RE.fullmatch(value) or _looks_identifying(value):
        return None
    return value if value in SAFE_OPERATION_NAMES else None


def _split_url(value: str) -> SplitResult:
    """Parse a URL without allowing malformed input to escape sanitization."""
    try:
        return urlsplit(value)
    except ValueError:
        return urlsplit("")


def _is_sensitive_operation(operation: str | None, suboperation: str | None) -> bool:
    """Identify operations whose bodies must be reduced to minimal metadata."""
    combined = f"{operation or ''}.{suboperation or ''}".casefold()
    return any(part in combined for part in SENSITIVE_OPERATION_PARTS)


def _content_text(content: Mapping[str, Any]) -> str | None:
    """Decode a bounded HAR response body in memory."""
    text = content.get("text")
    if not isinstance(text, str):
        return None
    if content.get("encoding") == "base64":
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            result = None
        else:
            if len(raw) > MAX_BODY_BYTES:
                return None
            try:
                result = raw.decode("utf-8")
            except UnicodeDecodeError:
                result = None
    elif len(text.encode(errors="ignore")) <= MAX_BODY_BYTES:
        result = text
    else:
        result = None
    return result


def _response_structure(response: Mapping[str, Any]) -> dict[str, Any]:
    """Parse only JSON response bodies and return their value-free structure."""
    parsed, value = _response_json(response)
    if not parsed:
        return value
    return describe_structure(value)


def _describe_json_rpc_response(value: Any) -> dict[str, Any]:
    """Describe JSON-RPC data and retain only a validated error.code integer."""
    structure = describe_structure(value)
    if not isinstance(value, Mapping) or value.get("jsonrpc") != "2.0":
        return structure
    error = value.get("error")
    if (
        not isinstance(error, Mapping)
        or "result" in value
        or not isinstance(error.get("message"), str)
    ):
        return structure
    code = error.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        return structure

    fields = structure.get("fields")
    error_structure = fields.get("error") if isinstance(fields, dict) else None
    error_fields = (
        error_structure.get("fields") if isinstance(error_structure, dict) else None
    )
    code_structure = (
        error_fields.get("code") if isinstance(error_fields, dict) else None
    )
    if isinstance(code_structure, dict) and code_structure.get("type") == "integer":
        code_structure["value"] = code
    return structure


def _json_rpc_response_structure(response: Mapping[str, Any]) -> dict[str, Any]:
    """Parse a confirmed JSON-RPC response using its narrow error exception."""
    parsed, value = _response_json(response)
    if not parsed:
        return value
    return _describe_json_rpc_response(value)


def _response_json(response: Mapping[str, Any]) -> tuple[bool, Any]:
    """Return a parsed JSON body or a safe structural omission marker."""
    content = response.get("content")
    if not isinstance(content, Mapping):
        return False, {"type": "missing"}
    text = _content_text(content)
    if text is None:
        return False, {"type": "missing"}
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False, {"type": "omitted", "reason": "non_json_or_unparseable"}
    return True, value


def _feature_map_structure(value: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize a large feature map without reproducing every flag schema."""
    safe_names = sorted(
        name
        for raw_name in value
        if (name := _safe_name(raw_name)) is not None and not _is_sensitive_key(name)
    )
    type_counts = Counter(_value_type(item) for item in value.values())
    return {
        "type": "object",
        "profile": "feature_flag_map",
        "field_count": len(value),
        "field_names": safe_names[:MAX_FEATURE_FIELD_NAMES],
        "field_names_truncated": len(safe_names) > MAX_FEATURE_FIELD_NAMES,
        "redacted_field_count": len(value) - len(safe_names),
        "value_type_counts": dict(sorted(type_counts.items())),
    }


def _feature_response_structure(response: Mapping[str, Any]) -> dict[str, Any]:
    """Return a bounded response schema for getNasFeature."""
    parsed, value = _response_json(response)
    if not parsed:
        return value
    if not isinstance(value, Mapping):
        return describe_structure(value)
    result = value.get("result")
    if not isinstance(result, Mapping):
        return describe_structure(value)
    envelope = _describe_json_rpc_response(value)
    fields = envelope.get("fields")
    if isinstance(fields, dict):
        fields["result"] = _feature_map_structure(result)
    return envelope


def _compact_legacy_feature_array(items: Sequence[Any]) -> dict[str, Any]:
    """Describe a legacy getFeatures array without repeating item schemas."""
    structure: dict[str, Any] = {"type": "array", "length": len(items)}
    if not items:
        return structure

    def value_free_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
        """Remove retained scalar values while preserving a field named value."""
        result: dict[str, Any] = {}
        is_schema_node = isinstance(schema.get("type"), str)
        for name, item in schema.items():
            if name == "value" and is_schema_node:
                continue
            if isinstance(item, Mapping):
                result[name] = value_free_schema(item)
            elif isinstance(item, list):
                result[name] = [
                    value_free_schema(member) if isinstance(member, Mapping) else member
                    for member in item
                ]
            else:
                result[name] = item
        return result

    schemas: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for item in items:
        item_schema = value_free_schema(describe_structure(item))
        fingerprint = json.dumps(
            item_schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        schemas[fingerprint] = item_schema
        counts[fingerprint] += 1

    fingerprints = sorted(counts)
    if len(fingerprints) == 1:
        structure["item_schema"] = schemas[fingerprints[0]]
        return structure

    structure["schema_variant_count"] = len(fingerprints)
    structure["item_schema_variants"] = [
        {"count": counts[fingerprint], "schema": schemas[fingerprint]}
        for fingerprint in fingerprints[:MAX_LEGACY_FEATURE_SCHEMA_VARIANTS]
    ]
    if len(fingerprints) > MAX_LEGACY_FEATURE_SCHEMA_VARIANTS:
        structure["schema_variants_truncated"] = True
    return structure


def _legacy_features_response_structure(response: Mapping[str, Any]) -> dict[str, Any]:
    """Describe getFeatures while compacting only its feature data array."""
    parsed, value = _response_json(response)
    if not parsed:
        return value

    structure = describe_structure(value)
    if not isinstance(value, Mapping) or not isinstance(value.get("data"), list):
        return structure

    fields = structure.get("fields")
    if isinstance(fields, dict):
        fields["data"] = _compact_legacy_feature_array(value["data"])
    return structure


def _safe_mime_type(response: Mapping[str, Any]) -> str | None:
    """Return only an allowlisted MIME type without parameters."""
    content = response.get("content")
    if not isinstance(content, Mapping):
        return None
    mime_type = content.get("mimeType")
    if not isinstance(mime_type, str):
        return None
    normalized = mime_type.partition(";")[0].strip().casefold()
    return normalized if normalized in SAFE_MIME_TYPES else "<other>"


def _raw_mime_type(response: Mapping[str, Any]) -> str:
    """Return a normalized MIME type for internal classification only."""
    content = response.get("content")
    if not isinstance(content, Mapping):
        return ""
    mime_type = content.get("mimeType")
    if not isinstance(mime_type, str):
        return ""
    return mime_type.partition(";")[0].strip().casefold()


def _unknown_entry_category(
    path: str,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> str:
    """Classify an unrecognized URL without retaining its path."""
    lowered_path = path.casefold()
    mime_type = _raw_mime_type(response)
    if (
        any(lowered_path.endswith(extension) for extension in STATIC_EXTENSIONS)
        or mime_type in STATIC_MIME_TYPES
        or mime_type.startswith(STATIC_MIME_PREFIXES)
    ):
        return "static_resource"
    post_data = request.get("postData")
    method = request.get("method")
    has_body = isinstance(post_data, Mapping) and isinstance(post_data.get("text"), str)
    if (
        isinstance(method, str)
        and method.upper() in {"POST", "PUT", "PATCH"}
        and has_body
    ) or mime_type in {"application/json", "text/json"}:
        return "unknown_protocol_candidate"
    return "unknown_web_request"


def _origin_placeholder(
    url: str, origins: dict[tuple[str, str, int | None], str]
) -> str:
    """Map an origin to a stable anonymous label without exposing it."""
    parsed = _split_url(url)
    try:
        port = parsed.port
    except ValueError:
        port = None
    key = (parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port)
    if key not in origins:
        origins[key] = f"<device-{len(origins) + 1}>"
    return origins[key]


def _basic_transaction(
    index: int,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    origin: str,
) -> dict[str, Any]:
    """Create metadata common to recognized and unknown transactions."""
    method = request.get("method")
    safe_method = method.upper() if isinstance(method, str) else "<unknown>"
    if safe_method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        safe_method = "<other>"
    status = response.get("status")
    return {
        "sequence": index,
        "device": origin,
        "http_method": safe_method,
        "http_status": status if isinstance(status, int) else None,
    }


def _json_rpc_transaction(
    transaction: dict[str, Any],
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> str | None:
    """Add one confirmed JSON-RPC operation or return its skip category."""
    post_data = request.get("postData")
    body = _post_json(post_data) if isinstance(post_data, Mapping) else None
    if body is None:
        return "unparseable_json_rpc_request"
    operation = _safe_operation(body.get("method"))
    if operation not in CONFIRMED_JSON_RPC_METHODS:
        return "unrecognized_json_rpc_method"

    transaction["protocol"] = "modern_json_rpc"
    transaction["endpoint_path"] = JSON_RPC_PATH
    transaction["known_operation"] = True
    if operation in CONFIRMED_JSON_RPC_AUTH_METHODS:
        transaction["api_method"] = "<authentication-operation>"
        transaction["operation_category"] = "authentication"
    elif operation in CONFIRMED_JSON_RPC_SENSITIVE_METHODS:
        transaction["api_method"] = "<sensitive-operation>"
        transaction["operation_category"] = "state_changing"
    else:
        transaction["api_method"] = operation
        transaction["operation_category"] = "read_only"
    url = request.get("url")
    query = _split_url(url).query if isinstance(url, str) else ""
    query_parameters = _parameter_summary(_query_parameters(request, query))
    if query_parameters:
        transaction["query_parameters"] = query_parameters
    sensitive = operation in (
        CONFIRMED_JSON_RPC_AUTH_METHODS | CONFIRMED_JSON_RPC_SENSITIVE_METHODS
    )
    if not sensitive:
        transaction["jsonrpc_version_type"] = _value_type(body.get("jsonrpc"))
        transaction["request_id_type"] = _value_type(body.get("id"))
        transaction["parameters"] = describe_structure(body.get("params"), key="params")
    if sensitive:
        transaction["response_structure"] = {
            "type": "omitted",
            "reason": "authentication_or_sensitive_operation",
        }
    elif operation == "getNasFeature":
        transaction["response_structure"] = _feature_response_structure(response)
    else:
        transaction["response_structure"] = _json_rpc_response_structure(response)
    return None


def _cgi_transaction(
    transaction: dict[str, Any],
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> str | None:
    """Add one confirmed CGI operation or return its skip category."""
    post_data = request.get("postData")
    parameters = _form_parameters(post_data) if isinstance(post_data, Mapping) else []
    url = request.get("url")
    query = _split_url(url).query if isinstance(url, str) else ""
    parameters.extend(_query_parameters(request, query))
    action_value = next(
        (
            item.get("value")
            for item in parameters
            if str(item.get("name", "")).casefold() == "bufaction"
        ),
        None,
    )
    suboperation_value = next(
        (
            item.get("value")
            for item in parameters
            if str(item.get("name", "")).casefold() == "action"
        ),
        None,
    )
    operation = _safe_operation(action_value)
    suboperation = _safe_operation(suboperation_value)
    if operation not in KNOWN_CGI_ACTIONS:
        return "unrecognized_legacy_cgi_action"

    transaction["protocol"] = "legacy_cgi"
    transaction["endpoint_path"] = LEGACY_CGI_PATH
    transaction["known_operation"] = True
    if operation in CGI_AUTH_OPERATIONS:
        transaction["cgi_action"] = "<authentication-operation>"
        transaction["operation_category"] = "authentication"
    elif operation in CGI_SENSITIVE_OPERATIONS or _is_sensitive_operation(
        operation, suboperation
    ):
        transaction["cgi_action"] = "<sensitive-operation>"
        transaction["operation_category"] = "state_changing"
    else:
        transaction["cgi_action"] = operation
        transaction["operation_category"] = "read_only"
        transaction["parameters"] = _parameter_summary(parameters)
    sensitive = (
        operation in CGI_AUTH_OPERATIONS or operation in CGI_SENSITIVE_OPERATIONS
    )
    if sensitive:
        transaction["response_structure"] = {
            "type": "omitted",
            "reason": "authentication_or_sensitive_operation",
        }
    elif operation == "getFeatures":
        transaction["response_structure"] = _legacy_features_response_structure(
            response
        )
    else:
        transaction["response_structure"] = _response_structure(response)
    return None


def _unknown_transaction(
    transaction: dict[str, Any],
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> None:
    """Retain only counts and types for an unassessed protocol."""
    transaction["protocol"] = "unknown"
    transaction["endpoint_path"] = "<unknown-endpoint>"
    query = request.get("queryString")
    post_data = request.get("postData")
    transaction["safe_metadata"] = {
        "query_parameter_count": len(query) if isinstance(query, list) else 0,
        "has_request_body": isinstance(post_data, Mapping)
        and isinstance(post_data.get("text"), str),
        "has_response_body": isinstance(response.get("content"), Mapping)
        and isinstance(response["content"].get("text"), str),
    }
    transaction["response_structure"] = {
        "type": "omitted",
        "reason": "unknown_protocol",
    }


def _finish_transaction(
    transaction: dict[str, Any],
    response: Mapping[str, Any],
    state: _AnalysisState,
) -> None:
    """Add safe common metadata and retain one included transaction."""
    mime_type = _safe_mime_type(response)
    if mime_type is not None:
        transaction["response_mime_type"] = mime_type
    transaction["removed_or_generalized"] = [
        "full_url_and_origin",
        "headers_and_cookies",
        "free_form_values",
    ]
    state.transactions.append(transaction)


def _process_known_entry(
    index: int,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    safe_url: str,
    *,
    protocol: str,
    state: _AnalysisState,
) -> None:
    """Process one request to a confirmed Buffalo API endpoint."""
    state.detected_protocol_entries[protocol] += 1
    origin = _origin_placeholder(safe_url, state.origins)
    transaction = _basic_transaction(index, request, response, origin)
    if protocol == "modern_json_rpc":
        skip_category = _json_rpc_transaction(transaction, request, response)
    else:
        skip_category = _cgi_transaction(transaction, request, response)
    if skip_category is not None:
        state.skipped[skip_category] += 1
        return
    _finish_transaction(transaction, response, state)


def _process_har_entry(
    index: int,
    raw_entry: Any,
    state: _AnalysisState,
) -> None:
    """Classify and sanitize one HAR entry without retaining unknown URLs."""
    if not isinstance(raw_entry, Mapping):
        state.skipped["malformed_entry"] += 1
        return
    request = raw_entry.get("request")
    response = raw_entry.get("response")
    if not isinstance(request, Mapping) or not isinstance(response, Mapping):
        state.skipped["malformed_entry"] += 1
        return
    url = request.get("url")
    safe_url = url if isinstance(url, str) else ""
    path = unquote(_split_url(safe_url).path)
    if path.rstrip("/") == JSON_RPC_PATH.rstrip("/"):
        _process_known_entry(
            index,
            request,
            response,
            safe_url,
            protocol="modern_json_rpc",
            state=state,
        )
        return
    if path == LEGACY_CGI_PATH:
        _process_known_entry(
            index,
            request,
            response,
            safe_url,
            protocol="legacy_cgi",
            state=state,
        )
        return
    category = _unknown_entry_category(path, request, response)
    if category == "unknown_protocol_candidate":
        state.unknown_candidates.append((index, request, response, safe_url))
    else:
        state.skipped[category] += 1


def _include_unknown_protocol_samples(state: _AnalysisState) -> int:
    """Retain bounded unknown candidates only when no known API was detected."""
    if state.detected_protocol_entries:
        state.skipped["unknown_protocol_candidate"] += len(state.unknown_candidates)
        return 0
    samples = state.unknown_candidates[:MAX_UNKNOWN_PROTOCOL_SAMPLES]
    for index, request, response, safe_url in samples:
        origin = _origin_placeholder(safe_url, state.origins)
        transaction = _basic_transaction(index, request, response, origin)
        _unknown_transaction(transaction, request, response)
        _finish_transaction(transaction, response, state)
    state.skipped["unknown_protocol_candidate_limit"] += max(
        0,
        len(state.unknown_candidates) - len(samples),
    )
    return len(samples)


def sanitize_har_document(
    har: Any,
    *,
    model: str | None = None,
    firmware: str | None = None,
) -> dict[str, Any]:
    """Convert a HAR mapping into a minimal diagnostic JSON document."""
    if not isinstance(har, Mapping):
        raise HarSanitizationError("Input is not a valid HAR document")
    log = har.get("log")
    if not isinstance(log, Mapping) or not isinstance(log.get("entries"), list):
        raise HarSanitizationError("Input is not a valid HAR document")

    state = _AnalysisState()
    for index, raw_entry in enumerate(log["entries"], start=1):
        _process_har_entry(index, raw_entry, state)

    unknown_samples_included = _include_unknown_protocol_samples(state)
    protocols = sorted(state.detected_protocol_entries)
    state.transactions.sort(key=lambda item: int(item["sequence"]))
    skipped_counts = {
        category: count for category, count in sorted(state.skipped.items()) if count
    }
    supplied = {
        key: value
        for key, value in {
            "device_model": _safe_user_value(model),
            "firmware_version": _safe_user_value(firmware),
        }.items()
        if value is not None
    }
    return {
        "format": "buffalo_compatibility_diagnostic",
        "format_version": FORMAT_VERSION,
        "user_supplied": supplied,
        "protocol_evidence": protocols or ["unknown"],
        "summary": {
            "har_entry_count": len(log["entries"]),
            "included_transaction_count": len(state.transactions),
            "skipped_entry_count": sum(skipped_counts.values()),
            "skipped_entries_by_category": skipped_counts,
            "detected_protocol_entries": dict(
                sorted(state.detected_protocol_entries.items())
            ),
            "unknown_protocol_samples_included": unknown_samples_included,
            "malformed_entry_count": skipped_counts.get("malformed_entry", 0),
            "anonymous_device_count": len(state.origins),
        },
        "privacy": {
            "review_required_before_sharing": True,
            "automatic_anonymity_guaranteed": False,
            "removed_or_generalized": list(REMOVAL_SUMMARY),
        },
        "transactions": state.transactions,
    }


def load_har(path: Path) -> Mapping[str, Any]:
    """Load a bounded HAR JSON object without echoing its contents."""
    try:
        input_size = path.stat().st_size
    except OSError as err:
        raise HarSanitizationError("HAR input could not be read as JSON") from err
    if input_size > MAX_INPUT_BYTES:
        raise HarSanitizationError("HAR input exceeds the supported size limit")
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        raise HarSanitizationError("HAR input could not be read as JSON") from err
    if not isinstance(value, Mapping):
        raise HarSanitizationError("HAR input must contain a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Create a minimal Buffalo API compatibility diagnostic from a local "
            "HAR file. No data is uploaded."
        )
    )
    parser.add_argument("input", type=Path, help="Original HAR file to inspect locally")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=f"Output JSON path (default: beside the HAR as {DEFAULT_OUTPUT_NAME})",
    )
    parser.add_argument("--model", help="Optional NAS model supplied by the user")
    parser.add_argument(
        "--firmware", help="Optional firmware version supplied by the user"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Sanitize one HAR locally and write a new, non-overwriting JSON document."""
    args = _parser().parse_args(argv)
    output = args.output or args.input.with_name(DEFAULT_OUTPUT_NAME)
    try:
        har = load_har(args.input)
        diagnostic = sanitize_har_document(
            har,
            model=args.model,
            firmware=args.firmware,
        )
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(diagnostic, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        print(
            "[ERROR] Output already exists; choose another --output path.",
            file=sys.stderr,
        )
        return 2
    except HarSanitizationError as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 2
    except OSError:
        print("[ERROR] The sanitized output could not be written.", file=sys.stderr)
        return 2

    summary = diagnostic["summary"]
    protocols = ", ".join(diagnostic["protocol_evidence"])
    print(
        f"[OK] Included {summary['included_transaction_count']} sanitized API "
        "transactions."
    )
    print(f"[INFO] Skipped {summary['skipped_entry_count']} irrelevant entries.")
    for category, count in summary["skipped_entries_by_category"].items():
        print(f"[INFO]   {category}: {count}")
    print(f"[INFO] Detected protocol evidence: {protocols}.")
    print(f"[INFO] Output: {output}")
    print("[WARNING] Automatic sanitization cannot guarantee anonymity.")
    print("[WARNING] Inspect the generated JSON carefully before sharing it.")
    print("[WARNING] Never share the original HAR file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
