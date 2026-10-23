"""Tests for the LS-WVL legacy CGI integration client."""

from __future__ import annotations

import json
from collections import deque
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from aiohttp import ClientConnectionError

from custom_components.buffalo_linkstation.api import BuffaloAuthenticationError
from custom_components.buffalo_linkstation.legacy_api import LegacyLinkStationApi

FIXTURE = Path(__file__).parent / "fixtures" / "ls_wvl_1_75_minimal.json"


class FakeResponse:
    """Minimal asynchronous response with an empty cookie collection."""

    def __init__(self, body, *, status=200):
        self.body = body
        self.status = status
        self.cookies = SimpleCookie()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self, *, content_type=None):
        return self.body


class FakeSession:
    """Return queued CGI envelopes and record request construction."""

    def __init__(self, outcomes):
        self.outcomes = {
            action: deque(value if isinstance(value, list) else [value])
            for action, value in outcomes.items()
        }
        self.requests = []

    def request(self, method, url, **kwargs):
        values = kwargs.get("params") or kwargs.get("data")
        action = values["bufaction"]
        self.requests.append((method, url, kwargs))
        queue = self.outcomes[action]
        outcome = queue[0] if len(queue) == 1 else queue.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def envelope(data=None, *, success=True, errors=None):
    return FakeResponse(
        {"success": success, "errors": errors or [], "data": data or []}
    )


def hardware_outcomes():
    capture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    outcomes = {
        action: FakeResponse(result["response"])
        for action, result in capture["methods"].items()
    }
    outcomes["verifyLogin"] = envelope([{"sid": "secret-session", "pageMode": "admin"}])
    return outcomes


async def test_native_login_posts_confirmed_fields_and_validates_cookie():
    session = FakeSession(hardware_outcomes())
    api = LegacyLinkStationApi(session, "nas.local", "admin", "changeme")

    await api.login()

    login_method, login_url, login_kwargs = session.requests[0]
    assert (login_method, login_url) == ("POST", "http://nas.local/dynamic.pl")
    assert login_kwargs["data"] == {
        "bufaction": "verifyLogin",
        "user": "admin",
        "password": "changeme",
    }
    validate_method, _url, validate_kwargs = session.requests[1]
    assert validate_method == "GET"
    assert validate_kwargs["params"] == {"bufaction": "validateSession"}
    assert validate_kwargs["headers"]["Cookie"].startswith("webui_session_admin=")
    assert "secret-session" in validate_kwargs["headers"]["Cookie"]


async def test_invalid_credentials_stop_before_session_validation():
    session = FakeSession(
        {"verifyLogin": envelope(success=False, errors=["invalid_login"])}
    )
    api = LegacyLinkStationApi(session, "nas.local", "admin", "changeme")

    with pytest.raises(BuffaloAuthenticationError):
        await api.login()

    assert len(session.requests) == 1


async def test_expired_session_establishes_one_fresh_confirmed_login():
    outcomes = hardware_outcomes()
    outcomes["verifyLogin"] = [
        envelope([{"sid": "first-session", "pageMode": "admin"}]),
        envelope([{"sid": "second-session", "pageMode": "admin"}]),
    ]
    outcomes["validateSession"] = [
        envelope([{"lang": "de"}]),
        envelope(success=False),
        envelope([{"lang": "de"}]),
    ]
    session = FakeSession(outcomes)
    api = LegacyLinkStationApi(session, "nas.local", "admin", "changeme")

    await api.login()
    await api.prepare_update()

    actions = [
        (kwargs.get("params") or kwargs.get("data"))["bufaction"]
        for _method, _url, kwargs in session.requests
    ]
    assert actions == [
        "verifyLogin",
        "validateSession",
        "validateSession",
        "verifyLogin",
        "validateSession",
    ]


async def test_hardware_capture_normalizes_only_confirmed_monitoring_fields():
    session = FakeSession(hardware_outcomes())
    api = LegacyLinkStationApi(session, "nas.local", "admin", "changeme")

    await api.prepare_update()
    features = await api.get_features()
    system_info = await api.get_system_info()
    devices = await api.get_devices()

    assert features["PRODUCT_NAME"] == "LS-WVL(KOUGYOKU)"
    assert system_info == {
        "product_name": "LS-WVL(KOUGYOKU)",
        "storage": {
            "used": 1_369_523_620,
            "total": 1_937_364_900,
            "unit": "vendor_reported_kb",
            "utilization": 70.69,
        },
    }
    array = next(item for item in devices if item["device_type"] == "array")
    assert array["status"] == "ok"
    assert array["raid_level"] == "raid1"
    assert array["members"] == ("disk1", "disk2")
    internal = [
        item
        for item in devices
        if item["device_type"] == "disk" and item["interface"] == "sata"
    ]
    assert [item["status"] for item in internal] == ["ok", "ok"]
    assert all(item["array"] == "array1" for item in internal)
    usb = next(item for item in devices if item.get("interface") == "usb")
    assert usb["status"] == "disconnect"
    assert "cpu_status" not in system_info
    assert "firmware" not in system_info
    assert "errors" not in system_info
    assert array["raw"]["sizeUsedRaw"] == ["1,369,523,620"]


async def test_multiple_legacy_clients_keep_sessions_isolated():
    first_outcomes = hardware_outcomes()
    first_outcomes["verifyLogin"] = envelope(
        [{"sid": "first-session", "pageMode": "admin"}]
    )
    second_outcomes = hardware_outcomes()
    second_outcomes["verifyLogin"] = envelope(
        [{"sid": "second-session", "pageMode": "admin"}]
    )
    first_session = FakeSession(first_outcomes)
    second_session = FakeSession(second_outcomes)
    first = LegacyLinkStationApi(first_session, "first.local", "admin", "changeme")
    second = LegacyLinkStationApi(
        second_session, "second.local", "operator", "changeme"
    )

    await first.login()
    await second.login()

    first_cookie = first_session.requests[1][2]["headers"]["Cookie"]
    second_cookie = second_session.requests[1][2]["headers"]["Cookie"]
    assert first_cookie.startswith("webui_session_admin=")
    assert "first-session" in first_cookie
    assert second_cookie.startswith("webui_session_operator=")
    assert "second-session" in second_cookie
    assert "second-session" not in first_cookie


async def test_read_recovers_after_one_temporary_connection_failure():
    outcomes = hardware_outcomes()
    outcomes["getFeatures"] = [
        ClientConnectionError("offline"),
        hardware_outcomes()["getFeatures"],
    ]
    session = FakeSession(outcomes)
    api = LegacyLinkStationApi(session, "nas.local", "admin", "changeme")

    await api.prepare_update()
    features = await api.get_features()

    assert features["PRODUCT_NAME"] == "LS-WVL(KOUGYOKU)"
    assert (
        sum(
            1
            for _method, _url, kwargs in session.requests
            if (kwargs.get("params") or kwargs.get("data"))["bufaction"]
            == "getFeatures"
        )
        == 2
    )


@pytest.mark.parametrize("method", ["shutdown", "reboot"])
async def test_power_operations_use_confirmed_form_only_after_explicit_call(method):
    outcomes = hardware_outcomes()
    outcomes["shutRestartLS"] = envelope()
    session = FakeSession(outcomes)
    api = LegacyLinkStationApi(session, "nas.local", "admin", "changeme")

    assert not session.requests
    await getattr(api, method)()

    request_method, _url, kwargs = session.requests[-1]
    assert request_method == "POST"
    assert kwargs["data"] == {
        "bufaction": "shutRestartLS",
        "action": method,
    }
    assert (
        sum(
            1
            for _request_method, _request_url, request_kwargs in session.requests
            if (request_kwargs.get("params") or request_kwargs.get("data"))["bufaction"]
            == "shutRestartLS"
        )
        == 1
    )
