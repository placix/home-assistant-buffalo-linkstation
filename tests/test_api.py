"""Tests for the standalone Buffalo JSON-RPC client."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from custom_components.buffalo_linkstation.api import (
    BuffaloAuthenticationError,
    BuffaloConnectionError,
    BuffaloInvalidResponseError,
    BuffaloJsonRpcError,
    BuffaloLinkStationApi,
    BuffaloTimeoutError,
    extract_identity,
    normalize_host,
)


class FakeResponse:
    def __init__(self, body: Any, status: int = 200, *, invalid_json: bool = False):
        self.body = body
        self.status = status
        self.invalid_json = invalid_json

    async def json(self, *, content_type=None):
        if self.invalid_json:
            raise json.JSONDecodeError("invalid", "x", 0)
        return self.body


class FakeContext:
    def __init__(self, result: Any):
        self.result = result

    async def __aenter__(self):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, handler: Callable[[dict[str, Any]], Any]):
        self.handler = handler
        self.requests: list[dict[str, Any]] = []

    def post(self, url, *, json, timeout, headers):
        self.requests.append({"url": url, "json": json, "headers": headers})
        return FakeContext(self.handler(json))


def result(payload: dict[str, Any], value: Any) -> FakeResponse:
    return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": value})


def login_then(method_results: dict[str, Any]):
    def handler(payload):
        if payload["method"] == "Auth.login":
            return result(payload, {"sid": "secret-session", "uid": 7})
        return result(payload, method_results[payload["method"]])

    return handler


async def test_successful_authentication_and_system_info():
    session = FakeSession(login_then({"get_portal_info_authneed": {"model": "LS220D"}}))
    api = BuffaloLinkStationApi(session, "NAS.LOCAL", "admin", "secret")

    assert await api.get_system_info() == {"model": "LS220D"}
    assert api.uid == 7
    assert session.requests[0]["url"] == "http://nas.local/nasapi/"
    assert session.requests[0]["json"]["params"] == {
        "username": "admin",
        "password": "secret",
    }
    assert session.requests[1]["json"]["params"] == {"sid": "secret-session"}
    assert session.requests[0]["json"]["id"] != session.requests[1]["json"]["id"]


def test_default_timeout_matches_common_firmware_lower_bound():
    api = BuffaloLinkStationApi(FakeSession(lambda _payload: None), "nas", "u", "p")
    assert api._timeout.total == 20


async def test_invalid_credentials():
    def handler(payload):
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": 401, "message": "Invalid credentials"},
            }
        )

    api = BuffaloLinkStationApi(FakeSession(handler), "nas", "admin", "bad")
    with pytest.raises(BuffaloAuthenticationError):
        await api.login()


async def test_session_expiration_reauthenticates_once():
    login_count = 0
    read_count = 0

    def handler(payload):
        nonlocal login_count, read_count
        if payload["method"] == "Auth.login":
            login_count += 1
            return result(payload, {"sid": f"sid-{login_count}"})
        read_count += 1
        if read_count == 1:
            return FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -119, "message": "Session ID is invalid"},
                }
            )
        return result(payload, {"hostname": "nas"})

    session = FakeSession(handler)
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    assert await api.get_system_info() == {"hostname": "nas"}
    assert login_count == 2
    assert session.requests[-1]["json"]["params"]["sid"] == "sid-2"


async def test_read_only_network_failure_retries_once():
    attempts = 0

    def handler(payload):
        nonlocal attempts
        if payload["method"] == "Auth.login":
            return result(payload, {"sid": "sid"})
        attempts += 1
        if attempts == 1:
            return ClientConnectionError("offline")
        return result(payload, {})

    api = BuffaloLinkStationApi(FakeSession(handler), "nas", "admin", "secret")
    assert await api.get_features() == {}
    assert attempts == 2


async def test_get_features_uses_exact_session_independent_parameters():
    session = FakeSession(lambda payload: result(payload, {"SUPPORT_RAID": 1}))
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")

    assert await api.get_features() == {"SUPPORT_RAID": 1}
    assert len(session.requests) == 1
    request = session.requests[0]["json"]
    assert request["jsonrpc"] == "2.0"
    assert request["method"] == "getNasFeature"
    assert request["params"] == {}
    assert isinstance(request["id"], str)


async def test_network_failure_is_wrapped():
    session = FakeSession(lambda payload: ClientConnectionError("offline"))
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    with pytest.raises(BuffaloConnectionError):
        await api.login()


async def test_timeout_is_wrapped():
    session = FakeSession(lambda payload: TimeoutError())
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    with pytest.raises(BuffaloTimeoutError):
        await api.login()


async def test_malformed_response_is_rejected():
    session = FakeSession(lambda payload: FakeResponse("not-json-rpc"))
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    with pytest.raises(BuffaloInvalidResponseError):
        await api.login()


async def test_invalid_json_is_rejected():
    session = FakeSession(lambda payload: FakeResponse(None, invalid_json=True))
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    with pytest.raises(BuffaloInvalidResponseError):
        await api.login()


async def test_mismatched_response_id_is_rejected():
    session = FakeSession(
        lambda payload: FakeResponse({"jsonrpc": "2.0", "id": "other", "result": {}})
    )
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    with pytest.raises(BuffaloInvalidResponseError):
        await api.login()


async def test_json_rpc_error_preserves_only_code_and_message():
    def handler(payload):
        if payload["method"] == "Auth.login":
            return result(payload, {"sid": "sid"})
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": "private",
                },
            }
        )

    api = BuffaloLinkStationApi(FakeSession(handler), "nas", "admin", "secret")
    with pytest.raises(BuffaloJsonRpcError, match="Method not found") as caught:
        await api.get_features()
    assert "private" not in str(caught.value)


async def test_non_session_error_is_not_misclassified_by_message_text():
    def handler(payload):
        if payload["method"] == "Auth.login":
            return result(payload, {"sid": "sid"})
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": -32603, "message": "Session database failure"},
            }
        )

    api = BuffaloLinkStationApi(FakeSession(handler), "nas", "admin", "secret")
    with pytest.raises(BuffaloJsonRpcError) as caught:
        await api.get_features()
    assert caught.value.code == -32603


@pytest.mark.parametrize("method", ["shutdown", "reboot"])
async def test_power_request_construction_and_no_timeout_retry(method):
    command_attempts = 0

    def handler(payload):
        nonlocal command_attempts
        if payload["method"] == "Auth.login":
            return result(payload, {"sid": "sid"})
        command_attempts += 1
        return TimeoutError()

    session = FakeSession(handler)
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")
    with pytest.raises(BuffaloTimeoutError):
        await getattr(api, method)()
    command = session.requests[-1]["json"]
    expected = "System.shutdown" if method == "shutdown" else "System.reboot"
    assert command["method"] == expected
    assert command["params"] == {"sid": "sid"}
    assert command_attempts == 1


def test_normalize_host_and_identity_for_models():
    assert normalize_host(" HTTP://NAS.Local/ ") == "nas.local"
    with pytest.raises(ValueError):
        normalize_host("http://nas.local/path")

    ls220 = extract_identity(
        {"device": {"model_name": "LS220D", "serial_no": "ABC", "version": "1.86"}},
        "nas",
    )
    ls420 = extract_identity(
        {"product_name": "LS420D", "firmware_version": "1.88", "host_name": "backup"},
        "nas",
    )
    assert (ls220.model, ls220.serial_number, ls220.firmware) == (
        "LS220D",
        "ABC",
        "1.86",
    )
    assert (ls420.model, ls420.hostname, ls420.firmware) == (
        "LS420D",
        "backup",
        "1.88",
    )


async def test_confirmed_read_method_names_and_parameters():
    session = FakeSession(
        login_then(
            {
                "get_service_status": {"service_status": {"smb": 3}},
                "device.getlist": {"devices": [{"device_name": "disk1"}]},
                "share.getlist": {"shares": [{"name": "private"}]},
            }
        )
    )
    api = BuffaloLinkStationApi(session, "nas", "admin", "secret")

    assert await api.get_service_status("smb") is True
    assert await api.get_devices() == [{"device_name": "disk1"}]
    assert await api.get_shares() == [{"name": "private"}]
    calls = {request["json"]["method"]: request["json"] for request in session.requests}
    assert calls["get_service_status"]["params"] == {
        "service_name": "smb",
        "sid": "secret-session",
    }
    assert calls["device.getlist"]["params"] == {"sid": "secret-session"}
    assert calls["share.getlist"]["params"] == {"sid": "secret-session"}


def test_identity_uses_confirmed_portal_fields():
    identity = extract_identity(
        {
            "hostname": "nas",
            "product_name": "LS420D",
            "fw_version_major": "1.88",
            "fw_version_minor": "0.01",
            "serial_number": "ABC",
        },
        "fallback",
    )
    assert identity == identity.__class__(
        hostname="nas", model="LS420D", firmware="1.88", serial_number="ABC"
    )


def test_identity_uses_feature_model_without_mistaking_disk_product_name():
    identity = extract_identity(
        {
            "hostname": "nas",
            "fw_version_major": "1.86",
            "disks": [{"product_name": "WDC DRIVE MODEL"}],
        },
        "fallback",
        {"PRODUCT_NAME": "LS220D(SANJO)"},
    )
    assert identity.model == "LS220D(SANJO)"

    missing_model = extract_identity(
        {"disks": [{"product_name": "WDC DRIVE MODEL"}]},
        "fallback",
    )
    assert missing_model.model is None
