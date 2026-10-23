"""Asynchronous client for the LS-WVL legacy CGI interface."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from http.cookies import Morsel
from typing import Any, Final, Literal

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout, CookieJar
from yarl import URL

from .api import (
    BuffaloAuthenticationError,
    BuffaloConnectionError,
    BuffaloInvalidResponseError,
    BuffaloLinkStationError,
    BuffaloTimeoutError,
    normalize_host,
)
from .const import DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_LOGIN: Final = "verifyLogin"
_VALIDATE: Final = "validateSession"
_FEATURES: Final = "getFeatures"
_DISKS: Final = "getDiskAllList"
_ARRAYS: Final = "getArrayList"
_POWER: Final = "shutRestartLS"
_COOKIE_NAME_RE: Final = re.compile(r"^webui_session_[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_REPORTED_KB_RE: Final = re.compile(r"^([0-9][0-9,]*) KB$")
_PERCENT_RE: Final = re.compile(r"^([0-9]+(?:\.[0-9]+)?) %$")


class BuffaloLegacyCgiError(BuffaloLinkStationError):
    """Raised when a legacy CGI operation reports an explicit error."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _session_cookie_name(username: str) -> str:
    name = f"webui_session_{username}"
    if not username or not _COOKIE_NAME_RE.fullmatch(name):
        raise BuffaloAuthenticationError("Username cannot be used for Web UI login")
    return name


def _reported_number(value: Any, pattern: re.Pattern[str]) -> float | int | None:
    if (
        not isinstance(value, str)
        or (match := pattern.fullmatch(value.strip())) is None
    ):
        return None
    normalized = match.group(1).replace(",", "")
    try:
        number = float(normalized)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


class LegacyLinkStationApi:
    """Restricted client for LS-WVL firmware's `/dynamic.pl` CGI API."""

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
        self._session = session
        self.host = normalize_host(host)
        self._username = username
        self._password = password
        self._timeout = ClientTimeout(total=timeout)
        self._read_attempts = max(1, read_attempts)
        self._cookies = CookieJar(unsafe=True)
        self._authenticated = False
        self._login_lock = asyncio.Lock()
        self._features: dict[str, Any] | None = None
        self._poll_disks: list[dict[str, Any]] | None = None

    @property
    def endpoint(self) -> str:
        """Return the fixed legacy CGI endpoint."""
        return f"http://{self.host}/dynamic.pl"

    def _cookie_header(self) -> str | None:
        cookies = self._cookies.filter_cookies(URL(self.endpoint))
        values = [morsel.OutputString() for morsel in cookies.values()]
        return "; ".join(values) or None

    def _store_response_cookies(self, response: ClientResponse) -> None:
        cookies: dict[str, Morsel[str] | str] = dict(response.cookies)
        if cookies:
            self._cookies.update_cookies(cookies, response_url=URL(self.endpoint))

    async def login(self, *, force: bool = False) -> None:
        """Establish and validate the hardware-confirmed Web UI session."""
        async with self._login_lock:
            if self._authenticated and not force:
                return
            data = await self._request(
                _LOGIN,
                "POST",
                extra={"user": self._username, "password": self._password},
            )
            login_data = _mapping(data[0]) if data else {}
            sid = login_data.get("sid")
            page_mode = login_data.get("pageMode")
            if not isinstance(sid, str) or not sid or page_mode in (None, ""):
                raise BuffaloAuthenticationError(
                    "Authentication response did not contain a session"
                )
            cookie_name = _session_cookie_name(self._username)
            self._cookies.update_cookies(
                {cookie_name: f"{sid}_en_{page_mode}"},
                response_url=URL(self.endpoint),
            )
            self._authenticated = False
            await self._validate_once()
            self._authenticated = True

    async def _validate_once(self) -> None:
        await self._request(_VALIDATE, "GET")

    async def prepare_update(self) -> None:
        """Validate the session once per poll and re-login after explicit expiry."""
        self._poll_disks = None
        if not self._authenticated:
            await self.login()
            return
        try:
            await self._validate_once()
        except BuffaloAuthenticationError:
            self._authenticated = False
            await self.login(force=True)

    async def _read(self, action: str, method: Literal["GET", "POST"]) -> list[Any]:
        if not self._authenticated:
            await self.login()
        attempt = 0
        reauthenticated = False
        while True:
            attempt += 1
            try:
                return await self._request(action, method)
            except BuffaloAuthenticationError:
                if reauthenticated:
                    raise
                self._authenticated = False
                await self.login(force=True)
                reauthenticated = True
            except (BuffaloConnectionError, BuffaloTimeoutError):
                if attempt >= self._read_attempts:
                    raise
                await asyncio.sleep(0)

    async def _request(
        self,
        action: str,
        method: Literal["GET", "POST"],
        *,
        extra: Mapping[str, str] | None = None,
    ) -> list[Any]:
        request_data = {"bufaction": action, **dict(extra or {})}
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        if cookie_header := self._cookie_header():
            headers["Cookie"] = cookie_header
        kwargs: dict[str, Any] = {"headers": headers, "timeout": self._timeout}
        kwargs["params" if method == "GET" else "data"] = request_data
        _LOGGER.debug("Calling Buffalo legacy CGI action %s", action)
        try:
            async with self._session.request(
                method, self.endpoint, **kwargs
            ) as response:
                self._store_response_cookies(response)
                return await self._decode_response(action, response)
        except TimeoutError as err:
            raise BuffaloTimeoutError(f"Timed out calling {action}") from err
        except ClientError as err:
            raise BuffaloConnectionError(f"Unable to call {action}") from err

    @staticmethod
    async def _decode_response(action: str, response: ClientResponse) -> list[Any]:
        if response.status in (401, 403):
            raise BuffaloAuthenticationError("Authentication was rejected")
        if response.status >= 400:
            raise BuffaloConnectionError(f"NAS returned HTTP {response.status}")
        try:
            body = await response.json(content_type=None)
        except (json.JSONDecodeError, TypeError, ValueError) as err:
            raise BuffaloInvalidResponseError("NAS returned invalid JSON") from err
        if not isinstance(body, Mapping) or not isinstance(body.get("success"), bool):
            raise BuffaloInvalidResponseError("NAS returned an invalid CGI response")
        if body["success"] is not True:
            errors = body.get("errors")
            if (
                action in {_LOGIN, _VALIDATE}
                or not isinstance(errors, list)
                or not errors
            ):
                raise BuffaloAuthenticationError("Authentication session was rejected")
            raise BuffaloLegacyCgiError("Legacy CGI operation failed")
        data = body.get("data")
        if not isinstance(data, list):
            raise BuffaloInvalidResponseError("CGI response did not contain data")
        return data

    async def get_features(self) -> dict[str, Any]:
        """Return the legacy feature list as an ID/value mapping."""
        if self._features is None:
            data = await self._read(_FEATURES, "GET")
            self._features = {
                str(item["id"]): item.get("value")
                for value in data
                if (item := _mapping(value)).get("id") not in (None, "")
            }
        return dict(self._features)

    async def get_system_info(self) -> dict[str, Any]:
        """Return only identity and storage fields confirmed by hardware."""
        features = await self.get_features()
        self._poll_disks = [
            _mapping(value) for value in await self._read(_DISKS, "GET")
        ]
        system_info: dict[str, Any] = {"product_name": features.get("PRODUCT_NAME")}
        for disk in self._poll_disks:
            if not str(disk.get("disk", "")).casefold().startswith("array"):
                continue
            used = _reported_number(disk.get("amountUsed"), _REPORTED_KB_RE)
            total = _reported_number(disk.get("capacity"), _REPORTED_KB_RE)
            utilization = _reported_number(disk.get("percentUsed"), _PERCENT_RE)
            if used is not None and total is not None:
                system_info["storage"] = {
                    "used": used,
                    "total": total,
                    "unit": "vendor_reported_kb",
                    "utilization": utilization,
                }
                break
        return system_info

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return confirmed arrays and internal/USB disks in normalized records."""
        disks = self._poll_disks
        if disks is None:
            disks = [_mapping(value) for value in await self._read(_DISKS, "GET")]
        arrays = [_mapping(value) for value in await self._read(_ARRAYS, "GET")]
        devices: list[dict[str, Any]] = []
        membership: dict[str, tuple[str, str]] = {}
        for array in arrays:
            array_name = str(array.get("arrayName") or "")
            for key, state in (
                ("diskNormal", "ok"),
                ("diskError", "error"),
                ("diskSpare", "spare"),
            ):
                values = array.get(key)
                if isinstance(values, list):
                    membership.update(
                        (str(disk), (array_name, state)) for disk in values
                    )
            devices.append(
                {
                    "device_type": "array",
                    "name": array_name or None,
                    "status": array.get("arrayStatus"),
                    "raid_level": array.get("arrayMode"),
                    "members": tuple(
                        str(item) for item in array.get("diskTotal") or []
                    ),
                    "raw": array,
                }
            )
        for disk in disks:
            name = str(disk.get("disk") or "")
            if name.casefold().startswith("array"):
                continue
            is_usb = name.casefold().startswith("usb")
            array_name, member_state = membership.get(name, ("", ""))
            devices.append(
                {
                    "device_type": "disk",
                    "name": name or None,
                    "interface": "usb" if is_usb else "sata",
                    "status": disk.get("status") if is_usb else member_state or None,
                    "array": array_name or disk.get("status"),
                    "member_state": member_state or None,
                    "raw": disk,
                }
            )
        return devices

    async def _power(self, action: Literal["shutdown", "reboot"]) -> list[Any]:
        await self.prepare_update()
        return await self._request(_POWER, "POST", extra={"action": action})

    async def shutdown(self) -> list[Any]:
        """Request one explicitly enabled shutdown without retrying it."""
        return await self._power("shutdown")

    async def reboot(self) -> list[Any]:
        """Request one explicitly enabled reboot without retrying it."""
        return await self._power("reboot")
