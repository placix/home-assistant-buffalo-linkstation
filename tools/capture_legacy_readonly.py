"""Capture sanitized, read-only Buffalo LS-WVL legacy CGI responses."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import ipaddress
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar
from yarl import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIRECTORY: Final = PROJECT_ROOT / "reference" / "captures"
CAPTURE_PATH_NAME: Final = "ls-wvl_1_75.json"
DIAGNOSTIC_PATH_NAME: Final = "ls-wvl_1_75_diagnostic.json"
MODEL: Final = "LS-WVL"
FIRMWARE: Final = "1.75"
CAPTURE_FORMAT: Final = 1
REDACTED: Final = "<redacted>"
VALIDATE_SESSION: Final = "validateSession"
LOGIN_ACTION: Final = "verifyLogin"


@dataclass(frozen=True, slots=True)
class LegacyAction:
    """One source-confirmed read-only legacy CGI action."""

    method: Literal["GET", "POST"]


READ_ONLY_ACTIONS: Final[dict[str, LegacyAction]] = {
    "getFeatures": LegacyAction("GET"),
    "getDiskAllList": LegacyAction("GET"),
    "getArrayList": LegacyAction("GET"),
    "getArrayMaintenance": LegacyAction("POST"),
    "getArrayMiscInfo": LegacyAction("GET"),
    "getUPSSettings": LegacyAction("POST"),
    "getOperationStatus": LegacyAction("POST"),
}
_SESSION_ACTION: Final = LegacyAction("GET")
_LOGIN_ACTION: Final = LegacyAction("POST")
_COOKIE_NAME_RE: Final = re.compile(r"^webui_session_[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SENSITIVE_KEY_PARTS: Final = (
    "credential",
    "cookie",
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
    "secret",
    "session",
    "targetobject",
    "text",
    "token",
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


class LegacyDiagnosticError(Exception):
    """Base exception for expected legacy diagnostic failures."""


class LegacyAuthenticationError(LegacyDiagnosticError):
    """Raised when the temporary Web UI session is not accepted."""


class LegacyNetworkError(LegacyDiagnosticError):
    """Raised when the CGI endpoint cannot be reached."""


class LegacyTimeoutError(LegacyDiagnosticError):
    """Raised when an individual CGI request times out."""


class LegacyInvalidResponseError(LegacyDiagnosticError):
    """Raised when the CGI response does not use the expected JSON envelope."""


class LegacyHttpError(LegacyDiagnosticError):
    """Raised for a non-authentication HTTP failure."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"HTTP {status}")


class LegacyCgiError(LegacyDiagnosticError):
    """Raised when the legacy CGI reports an operation error."""

    def __init__(self, errors: list[Any]) -> None:
        self.errors = errors
        super().__init__("Legacy CGI operation failed")


@dataclass(frozen=True, slots=True)
class LegacyResult:
    """One successful HTTP and CGI response."""

    http_status: int
    response: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """Paths and completion state for one requested capture."""

    capture_path: Path
    diagnostic_path: Path | None
    complete: bool


def _normalize_host(host: str) -> str:
    """Return a host without allowing a path, query, fragment, or credentials."""
    normalized = host.strip().rstrip("/")
    if normalized.casefold().startswith("https://"):
        raise ValueError("Enter the host without a scheme; this tool uses local HTTP")
    if normalized.casefold().startswith("http://"):
        normalized = normalized[len("http://") :]
    if (
        not normalized
        or any(character in normalized for character in "/\\?#@")
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("Enter a hostname or IP address without a path")
    return normalized


def parse_session_cookie(cookie_pair: str) -> tuple[str, str]:
    """Validate one manually captured Web UI session cookie pair."""
    value = cookie_pair.strip()
    if value.count("=") < 1:
        raise ValueError("Enter the cookie as webui_session_<user>=<temporary value>")
    name, cookie_value = value.split("=", 1)
    if not _COOKIE_NAME_RE.fullmatch(name):
        raise ValueError("The cookie name must begin with webui_session_")
    if not cookie_value or any(char in cookie_value for char in ";\r\n"):
        raise ValueError("The temporary session cookie value is invalid")
    return name, cookie_value


def _session_cookie_name(username: str) -> str:
    """Return the firmware-defined session cookie name for one login user."""
    name = f"webui_session_{username}"
    if not username or not _COOKIE_NAME_RE.fullmatch(name):
        raise ValueError("The username cannot be represented in a Web UI cookie name")
    return name


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        part in normalized for part in _SENSITIVE_KEY_PARTS
    )


def _sanitize_string(value: str, entered_host: str) -> str:
    sanitized = value
    if entered_host:
        sanitized = re.sub(
            re.escape(entered_host), REDACTED, sanitized, flags=re.IGNORECASE
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
            str(item_key): sanitize(item_value, entered_host=entered_host, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [sanitize(item, entered_host=entered_host) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value, entered_host)
    return value


class LegacyCgiClient:
    """Restricted client for source-confirmed read-only LS-WVL CGI actions."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        *,
        timeout: float = 20.0,
    ) -> None:
        self._session = session
        self.host = _normalize_host(host)
        self._timeout = ClientTimeout(total=timeout)

    @property
    def endpoint(self) -> str:
        """Return the fixed legacy CGI endpoint."""
        return f"http://{self.host}/dynamic.pl"

    def set_session_cookie(self, cookie_pair: str) -> None:
        """Place one manually captured session cookie in the in-memory jar."""
        name, value = parse_session_cookie(cookie_pair)
        self._set_cookie(name, value)

    async def login(
        self,
        username: str,
        password: str,
        *,
        language: str = "en",
    ) -> None:
        """Authenticate with the browser-confirmed form and create its cookie."""
        cookie_name = _session_cookie_name(username)
        result = await self._request(
            LOGIN_ACTION,
            _LOGIN_ACTION,
            form_data={"user": username, "password": password},
        )
        data = result.response["data"]
        if not data or not isinstance(data[0], Mapping):
            raise LegacyAuthenticationError(
                "Login response did not contain session data"
            )
        login_data = data[0]
        sid = login_data.get("sid")
        page_mode = login_data.get("pageMode")
        if not isinstance(sid, str) or not sid or page_mode in (None, ""):
            raise LegacyAuthenticationError("Login response did not contain a session")

        # Preserve a server-created cookie when present. The collected Web UI
        # otherwise constructs SID_language_pageMode in JavaScript.
        cookies = self._session.cookie_jar.filter_cookies(URL(self.endpoint))
        if cookie_name not in cookies:
            self._set_cookie(
                cookie_name,
                f"{sid}_{language}_{page_mode}",
            )

    def _set_cookie(self, name: str, value: str) -> None:
        """Update only the session's in-memory cookie jar."""
        self._session.cookie_jar.update_cookies(
            {name: value},
            response_url=URL(self.endpoint),
        )

    async def validate_session(self) -> LegacyResult:
        """Validate the manually supplied session before data collection."""
        return await self._request(VALIDATE_SESSION, _SESSION_ACTION)

    async def call(self, action: str) -> LegacyResult:
        """Call one allowlisted read-only action."""
        try:
            specification = READ_ONLY_ACTIONS[action]
        except KeyError as err:
            raise ValueError("Action is not in the read-only allowlist") from err
        return await self._request(action, specification)

    async def _request(
        self,
        action: str,
        specification: LegacyAction,
        *,
        form_data: Mapping[str, str] | None = None,
    ) -> LegacyResult:
        request_data = {"bufaction": action}
        if form_data is not None:
            request_data.update(form_data)
        kwargs: dict[str, Any] = {
            "headers": {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
            "timeout": self._timeout,
        }
        if specification.method == "GET":
            kwargs["params"] = request_data
        else:
            kwargs["data"] = request_data
        try:
            async with self._session.request(
                specification.method, self.endpoint, **kwargs
            ) as response:
                return await self._decode_response(action, response)
        except TimeoutError as err:
            raise LegacyTimeoutError("Request timed out") from err
        except ClientError as err:
            raise LegacyNetworkError("Unable to reach the legacy CGI endpoint") from err

    @staticmethod
    async def _decode_response(action: str, response: Any) -> LegacyResult:
        if response.status in (401, 403):
            raise LegacyAuthenticationError("The Web UI session was rejected")
        if response.status >= 400:
            raise LegacyHttpError(response.status)
        try:
            body = await response.json(content_type=None)
        except (json.JSONDecodeError, TypeError, ValueError) as err:
            raise LegacyInvalidResponseError("Response was not valid JSON") from err
        if not isinstance(body, Mapping):
            raise LegacyInvalidResponseError("Response was not a JSON object")
        payload = dict(body)
        success = payload.get("success")
        if not isinstance(success, bool):
            raise LegacyInvalidResponseError("Response did not contain boolean success")
        if not success:
            errors = payload.get("errors")
            error_items = list(errors) if isinstance(errors, list) else []
            if action in {LOGIN_ACTION, VALIDATE_SESSION} or not error_items:
                raise LegacyAuthenticationError("The Web UI session is not valid")
            raise LegacyCgiError(error_items)
        data = payload.get("data")
        if not isinstance(data, list):
            raise LegacyInvalidResponseError("Response did not contain a data array")
        return LegacyResult(response.status, payload)


def _failure_record(
    action: str, error: LegacyDiagnosticError, host: str
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "method": action,
        "error_type": type(error).__name__,
    }
    if isinstance(error, LegacyHttpError):
        record["http_status"] = error.status
    if isinstance(error, LegacyCgiError):
        record["cgi_errors"] = sanitize(error.errors, entered_host=host)
    return record


def _report_failure(
    action: str,
    error: LegacyDiagnosticError,
    reporter: Callable[[str], None],
) -> None:
    if isinstance(error, LegacyHttpError):
        detail = f"HTTP {error.status}"
    elif isinstance(error, LegacyCgiError):
        detail = "CGI operation error"
    elif isinstance(error, LegacyAuthenticationError):
        detail = "authentication failed"
    elif isinstance(error, LegacyTimeoutError):
        detail = "request timed out"
    elif isinstance(error, LegacyNetworkError):
        detail = "network request failed"
    else:
        detail = "invalid response"
    reporter(f"[ERROR] {action}: {detail}.")


async def _collect_read_actions(
    client: LegacyCgiClient,
    entered_host: str,
    reporter: Callable[[str], None] = print,
    *,
    actions: Sequence[str] = tuple(READ_ONLY_ACTIONS),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run allowlisted reads independently and retain successful responses."""
    if not set(actions).issubset(READ_ONLY_ACTIONS):
        raise ValueError("Requested action is not in the read-only allowlist")
    successful: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    for action in actions:
        reporter(f"[INFO] Calling {action}...")
        try:
            result = await client.call(action)
        except LegacyDiagnosticError as err:
            failures.append(_failure_record(action, err, entered_host))
            _report_failure(action, err, reporter)
        else:
            successful[action] = {
                "http_status": result.http_status,
                "response": sanitize(result.response, entered_host=entered_host),
            }
            reporter("[OK] Request successful.")
    return successful, failures


def _capture_document(
    methods: Mapping[str, Any],
    *,
    authentication_mode: str,
    complete: bool,
) -> dict[str, Any]:
    return {
        "capture_format": CAPTURE_FORMAT,
        "model": MODEL,
        "firmware": FIRMWARE,
        "source": "hardware_read_only_legacy_cgi",
        "authentication": {
            "mode": authentication_mode,
            "validated": VALIDATE_SESSION in methods,
        },
        "complete": complete,
        "methods": dict(methods),
    }


def _diagnostic_document(failures: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "diagnostic_format": 1,
        "model": MODEL,
        "firmware": FIRMWARE,
        "capture_status": "partial",
        "failed_methods": [dict(failure) for failure in failures],
    }


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    """Write JSON only when no file already exists."""
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, indent=2, sort_keys=True, ensure_ascii=False)
        output.write("\n")


def _ensure_outputs_available(directory: Path) -> tuple[Path, Path]:
    capture_path = directory / CAPTURE_PATH_NAME
    diagnostic_path = directory / DIAGNOSTIC_PATH_NAME
    existing = next(
        (path for path in (capture_path, diagnostic_path) if path.exists()), None
    )
    if existing is not None:
        raise FileExistsError(
            f"Capture output already exists: {existing}. Move it aside before retrying."
        )
    return capture_path, diagnostic_path


def _save_capture_outputs(
    capture_directory: Path,
    capture_path: Path,
    diagnostic_path: Path,
    methods: Mapping[str, Any],
    failures: Sequence[Mapping[str, Any]],
    *,
    authentication_mode: str,
    complete: bool,
) -> Path | None:
    """Persist sanitized capture documents without blocking the event loop."""
    capture_directory.mkdir(parents=True, exist_ok=True)
    _write_json_exclusive(
        capture_path,
        _capture_document(
            methods,
            authentication_mode=authentication_mode,
            complete=complete,
        ),
    )
    if not failures:
        return None
    _write_json_exclusive(diagnostic_path, _diagnostic_document(failures))
    return diagnostic_path


async def _capture(
    host: str,
    request_timeout: float,
    *,
    username: str | None = None,
    password: str | None = None,
    cookie_pair: str | None = None,
    capture_directory: Path = CAPTURE_DIRECTORY,
    reporter: Callable[[str], None] = print,
    session_factory: Callable[..., Any] = ClientSession,
) -> CaptureOutcome:
    """Authenticate, validate the session, and capture allowlisted responses."""
    capture_path, diagnostic_path = _ensure_outputs_available(capture_directory)
    normalized_host = _normalize_host(host)
    native_login = username is not None or password is not None
    if native_login == (cookie_pair is not None):
        raise ValueError("Choose either native login or one temporary session cookie")
    if native_login and (not username or not password):
        raise ValueError("Username and password are required for native login")
    if cookie_pair is not None:
        parse_session_cookie(cookie_pair)
    authentication_mode = (
        "native_username_password"
        if native_login
        else "manual_temporary_session_cookie"
    )
    methods: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []

    cookie_jar = CookieJar(unsafe=True)
    async with session_factory(cookie_jar=cookie_jar) as session:
        client = LegacyCgiClient(
            session,
            normalized_host,
            timeout=request_timeout,
        )
        authenticated = False
        if native_login:
            reporter("[INFO] Authenticating...")
            try:
                await client.login(username, password)
            except LegacyDiagnosticError as err:
                failures.append(_failure_record(LOGIN_ACTION, err, normalized_host))
                _report_failure(LOGIN_ACTION, err, reporter)
            else:
                authenticated = True
                reporter("[OK] Login successful.")
        else:
            client.set_session_cookie(cookie_pair)
            authenticated = True

        if authenticated:
            reporter(f"[INFO] Calling {VALIDATE_SESSION}...")
            try:
                validation = await client.validate_session()
            except LegacyDiagnosticError as err:
                failures.append(_failure_record(VALIDATE_SESSION, err, normalized_host))
                _report_failure(VALIDATE_SESSION, err, reporter)
            else:
                methods[VALIDATE_SESSION] = {
                    "http_status": validation.http_status,
                    "response": sanitize(
                        validation.response, entered_host=normalized_host
                    ),
                }
                reporter("[OK] Session validation successful.")
                successful, read_failures = await _collect_read_actions(
                    client, normalized_host, reporter
                )
                methods.update(successful)
                failures.extend(read_failures)

    required = {VALIDATE_SESSION, *READ_ONLY_ACTIONS}
    complete = not failures and set(methods) == required
    saved_diagnostic = await asyncio.to_thread(
        _save_capture_outputs,
        capture_directory,
        capture_path,
        diagnostic_path,
        methods,
        failures,
        authentication_mode=authentication_mode,
        complete=complete,
    )
    return CaptureOutcome(capture_path, saved_diagnostic, complete)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture source-confirmed read-only LS-WVL 1.75 CGI responses using "
            "interactive credentials or a temporary Web UI cookie."
        )
    )
    parser.add_argument(
        "--manual-cookie",
        action="store_true",
        help=(
            "Use a masked temporary webui_session_* cookie instead of interactive "
            "username/password authentication."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help=(
            "Per-request timeout in seconds; authentication data is never "
            "accepted as an argument."
        ),
    )
    return parser.parse_args()


def _prompt_authentication(
    *, manual_cookie: bool
) -> tuple[str | None, str | None, str | None]:
    """Prompt for exactly one authentication mode and validate its input."""
    if manual_cookie:
        cookie_pair = getpass.getpass(
            "Temporary webui_session_* cookie as name=value (hidden): "
        )
        parse_session_cookie(cookie_pair)
        return None, None, cookie_pair

    username = input("NAS username: ").strip()
    password = getpass.getpass("NAS password: ")
    if not username or not password:
        raise ValueError("Host, username, and password are required")
    _session_cookie_name(username)
    return username, password, None


def _print_capture_plan(*, manual_cookie: bool, capture_path: Path) -> None:
    """Describe the fixed request plan without exposing authentication data."""
    if manual_cookie:
        print("The tool will use the temporary cookie only for this process.")
    else:
        print("The tool will authenticate with a POST to /dynamic.pl.")
    print("It will validate the session and call only these read-only actions:")
    print(f"  - {VALIDATE_SESSION}")
    for action in READ_ONLY_ACTIONS:
        print(f"  - {action}")
    print(f"A sanitized capture will be created at: {capture_path}")


def main() -> int:  # noqa: PLR0911 - explicit CLI outcomes keep failures clear
    """Prompt for private data and start only after explicit confirmation."""
    args = _parse_args()
    if args.timeout <= 0:
        print("Timeout must be greater than zero.", file=sys.stderr)
        return 2
    try:
        capture_path, _diagnostic_path = _ensure_outputs_available(CAPTURE_DIRECTORY)
    except FileExistsError as err:
        print(f"Capture not started: {err}", file=sys.stderr)
        return 1

    host = input("NAS hostname or IP address: ").strip()
    username: str | None = None
    password: str | None = None
    cookie_pair: str | None = None
    try:
        _normalize_host(host)
        username, password, cookie_pair = _prompt_authentication(
            manual_cookie=args.manual_cookie
        )
    except ValueError as err:
        print(f"Capture not started: {err}", file=sys.stderr)
        return 2

    _print_capture_plan(manual_cookie=args.manual_cookie, capture_path=capture_path)
    if input("Continue? [y/N]: ").strip().casefold() not in {"y", "yes"}:
        print("Cancelled; no NAS request was sent.")
        return 0

    try:
        capture_kwargs = (
            {"cookie_pair": cookie_pair}
            if args.manual_cookie
            else {"username": username, "password": password}
        )
        outcome = asyncio.run(_capture(host, args.timeout, **capture_kwargs))
    except (FileExistsError, OSError, ValueError) as err:
        print(f"Capture failed: {err}", file=sys.stderr)
        return 1
    finally:
        password = ""
        cookie_pair = ""

    if not outcome.complete:
        print(f"Sanitized partial capture saved to {outcome.capture_path}")
        print(f"Failure diagnostics saved to {outcome.diagnostic_path}")
        print("The capture is incomplete and must not be treated as validated data.")
        return 1
    print(f"Complete sanitized capture saved to {outcome.capture_path}")
    print("Review it manually before sharing; the reference directory is Git-ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
