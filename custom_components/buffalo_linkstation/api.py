"""Asynchronous JSON-RPC client for Buffalo LinkStation devices."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any, Final
from uuid import uuid4

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

from .const import DEFAULT_TIMEOUT
from .models import BuffaloIdentity

_LOGGER = logging.getLogger(__name__)

_SESSION_ERROR_CODE: Final = -119
_READ_ONLY_METHODS: Final = frozenset(
    {
        "Auth.get_uid",
        "User.detail",
        "User.get_username",
        "get_portal_info_authneed",
        "getNasFeature",
        "get_service_status",
        "share.getlist",
        "device.getlist",
        "Job.listJobs",
    }
)


class BuffaloLinkStationError(Exception):
    """Base exception for LinkStation client failures."""


class BuffaloAuthenticationError(BuffaloLinkStationError):
    """Raised when authentication fails or expires."""


class BuffaloConnectionError(BuffaloLinkStationError):
    """Raised when the LinkStation cannot be reached."""


class BuffaloTimeoutError(BuffaloLinkStationError):
    """Raised when the LinkStation does not respond in time."""


class BuffaloInvalidResponseError(BuffaloLinkStationError):
    """Raised when a response is not valid JSON-RPC."""


class BuffaloJsonRpcError(BuffaloLinkStationError):
    """Raised for a JSON-RPC error response."""

    def __init__(self, code: int | None, message: str) -> None:
        """Initialize the JSON-RPC error without retaining response data."""
        self.code = code
        self.message = message
        super().__init__(f"JSON-RPC error {code}: {message}")


class BuffaloUnsupportedOperationError(BuffaloLinkStationError):
    """Raised when an operation is not enabled for the device."""


def normalize_host(host: str) -> str:
    """Normalize a user-entered local hostname or IP address."""
    normalized = host.strip().rstrip("/")
    for prefix in ("http://", "https://"):
        if normalized.casefold().startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if "/" in normalized or not normalized:
        raise ValueError("Enter a hostname or IP address without a path")
    return normalized.casefold()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _find_value(data: Any, keys: tuple[str, ...]) -> str | None:
    """Find the first non-empty scalar under a known key, recursively."""
    wanted = {key.casefold() for key in keys}
    if isinstance(data, Mapping):
        for key, value in data.items():
            if str(key).casefold() in wanted and isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text:
                    return text
        for value in data.values():
            if result := _find_value(value, keys):
                return result
    elif isinstance(data, list):
        for value in data:
            if result := _find_value(value, keys):
                return result
    return None


def _find_direct_value(data: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    """Find a scalar only at the response root, excluding nested disk metadata."""
    wanted = {key.casefold() for key in keys}
    for key, value in data.items():
        if str(key).casefold() in wanted and isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return text
    return None


def extract_identity(
    system_info: Mapping[str, Any],
    fallback_host: str,
    features: Mapping[str, Any] | None = None,
) -> BuffaloIdentity:
    """Extract identity values without assuming a single firmware response shape."""
    # The UI presents the optional minor/build field separately. The major field
    # is the marketed firmware version used by Buffalo (for example, 1.88).
    firmware = _find_direct_value(system_info, ("fw_version_major",))
    model = _find_direct_value(system_info, ("model", "model_name", "product_name"))
    if model is None and features is not None:
        model = _find_direct_value(features, ("PRODUCT_NAME",))
    if model is None:
        # Some older response fixtures wrap identity in a single device object.
        # Exclude nested product_name here because disk records use that key too.
        model = _find_value(system_info, ("model", "model_name"))
    return BuffaloIdentity(
        hostname=_find_value(system_info, ("hostname", "host_name", "nas_name"))
        or fallback_host,
        model=model,
        firmware=firmware
        or _find_value(
            system_info, ("firmware", "firmware_version", "version", "fw_version")
        ),
        serial_number=_find_value(
            system_info, ("serial_number", "serial", "serial_no", "product_serial")
        ),
    )


class BuffaloLinkStationApi:
    """Client for the local Buffalo JSON-RPC 2.0 endpoint."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        username: str,
        password: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        read_attempts: int = 2,
    ) -> None:
        """Initialize a client. The supplied Home Assistant session is not owned."""
        self._session = session
        self.host = normalize_host(host)
        self._username = username
        self._password = password
        self._timeout = ClientTimeout(total=timeout)
        self._read_attempts = max(1, read_attempts)
        self._sid: str | None = None
        self._uid: str | int | None = None
        self._login_lock = asyncio.Lock()

    @property
    def endpoint(self) -> str:
        """Return the local JSON-RPC endpoint."""
        return f"http://{self.host}/nasapi/"

    @property
    def uid(self) -> str | int | None:
        """Return the authenticated user identifier, when supplied."""
        return self._uid

    async def login(self, *, force: bool = False) -> None:
        """Authenticate and retain the session ID only in memory."""
        async with self._login_lock:
            if self._sid is not None and not force:
                return
            try:
                result = await self._transport(
                    "Auth.login",
                    {"username": self._username, "password": self._password},
                )
            except BuffaloJsonRpcError as err:
                raise BuffaloAuthenticationError("Authentication was rejected") from err
            result_map = _mapping(result)
            sid = result_map.get("sid")
            if not isinstance(sid, str) or not sid:
                raise BuffaloAuthenticationError(
                    "Authentication response did not contain a session ID"
                )
            self._sid = sid
            uid = result_map.get("uid")
            if isinstance(uid, (str, int)):
                self._uid = uid

    async def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> Any:
        """Call one method, reauthenticating only after an explicit auth rejection."""
        if authenticated:
            await self.login()
        request_params = dict(params or {})
        if authenticated:
            request_params["sid"] = self._sid

        attempts = self._read_attempts if method in _READ_ONLY_METHODS else 1
        reauthenticated = False
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._transport(method, request_params)
            except BuffaloAuthenticationError:
                if not authenticated or reauthenticated:
                    raise
                self._sid = None
                await self.login(force=True)
                request_params["sid"] = self._sid
                reauthenticated = True
                # A rejected request was not accepted as an operation by the API.
                continue
            except (BuffaloConnectionError, BuffaloTimeoutError):
                if attempt >= attempts:
                    raise
                await asyncio.sleep(0)

    async def _transport(self, method: str, params: Mapping[str, Any]) -> Any:
        """Send a single request; never log parameters or response bodies."""
        request_id = uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        _LOGGER.debug("Calling Buffalo JSON-RPC method %s", method)
        try:
            async with self._session.post(
                self.endpoint,
                json=payload,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            ) as response:
                return await self._decode_response(response, request_id)
        except TimeoutError as err:
            raise BuffaloTimeoutError(f"Timed out calling {method}") from err
        except ClientError as err:
            raise BuffaloConnectionError(f"Unable to call {method}") from err

    async def _decode_response(self, response: ClientResponse, request_id: str) -> Any:
        if response.status in (401, 403):
            raise BuffaloAuthenticationError("Authentication was rejected")
        if response.status >= 400:
            raise BuffaloConnectionError(f"NAS returned HTTP {response.status}")
        try:
            body = await response.json(content_type=None)
        except (json.JSONDecodeError, ValueError, TypeError) as err:
            raise BuffaloInvalidResponseError("NAS returned invalid JSON") from err
        if not isinstance(body, Mapping) or body.get("jsonrpc") != "2.0":
            raise BuffaloInvalidResponseError(
                "NAS returned an invalid JSON-RPC response"
            )
        if str(body.get("id")) != request_id:
            raise BuffaloInvalidResponseError("JSON-RPC response ID did not match")
        if "error" in body:
            error = _mapping(body["error"])
            code_value = error.get("code")
            try:
                code = int(code_value)
            except (TypeError, ValueError):
                code = None
            message = str(error.get("message") or "Unknown error")
            if code == _SESSION_ERROR_CODE:
                raise BuffaloAuthenticationError("Authentication session was rejected")
            raise BuffaloJsonRpcError(code, message)
        if "result" not in body:
            raise BuffaloInvalidResponseError(
                "JSON-RPC response did not contain a result"
            )
        return body["result"]

    async def get_system_info(self) -> dict[str, Any]:
        """Return the authenticated portal/NAS information response."""
        return _mapping(await self._call("get_portal_info_authneed"))

    async def prepare_update(self) -> None:
        """Ensure the authenticated JSON-RPC session is ready for polling."""
        await self.login()

    async def get_features(self) -> dict[str, Any]:
        """Return session-independent advertised NAS feature flags."""
        return _mapping(await self._call("getNasFeature", authenticated=False))

    async def get_service_status(self, service_name: str) -> bool | None:
        """Return whether one named service's least-significant status bit is set."""
        result = _mapping(
            await self._call("get_service_status", {"service_name": service_name})
        )
        statuses = _mapping(result.get("service_status"))
        value = statuses.get(service_name)
        try:
            return bool(int(value) & 1)
        except (TypeError, ValueError):
            return None

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return disk, partition, and RAID device records."""
        result = _mapping(await self._call("device.getlist"))
        items = result.get("devices", [])
        if not isinstance(items, list):
            return []
        return [_mapping(item) for item in items if isinstance(item, Mapping)]

    async def get_shares(self) -> list[dict[str, Any]]:
        """Return the share list when the firmware exposes one."""
        result = await self._call("share.getlist")
        if isinstance(result, list):
            return [_mapping(item) for item in result if isinstance(item, Mapping)]
        result_map = _mapping(result)
        items = result_map.get("shares", result_map.get("list", []))
        if not isinstance(items, list):
            return []
        return [_mapping(item) for item in items if isinstance(item, Mapping)]

    async def shutdown(self) -> Any:
        """Request a shutdown exactly once after authentication succeeds."""
        return await self._call("System.shutdown")

    async def reboot(self) -> Any:
        """Request a reboot exactly once after authentication succeeds."""
        return await self._call("System.reboot")
