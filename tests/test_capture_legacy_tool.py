"""Tests for the read-only LS-WVL legacy CGI capture tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import capture_legacy_readonly as legacy

HARDWARE_FIXTURE = Path(__file__).parent / "fixtures" / "ls_wvl_1_75_minimal.json"
REAL_CAPTURE = (
    Path(__file__).resolve().parents[1] / "reference/captures/ls-wvl_1_75.json"
)


class FakeResponse:
    """Minimal asynchronous aiohttp response stand-in."""

    def __init__(self, payload=None, *, status=200, json_error=None):
        self.status = status
        self._payload = payload
        self._json_error = json_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def json(self, *, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    """Record requests and return action-specific outcomes."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.requests = []
        self.cookie_jar = legacy.CookieJar(unsafe=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def request(self, method, url, **kwargs):
        request_values = kwargs.get("params") or kwargs.get("data")
        action = request_values["bufaction"]
        self.requests.append((method, url, kwargs))
        outcome = self.outcomes[action]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def session_factory(session):
    """Bind the production-created in-memory cookie jar to a fake session."""

    def create_session(*, cookie_jar):
        session.cookie_jar = cookie_jar
        return session

    return create_session


def successful_response(data=None):
    """Build one successful legacy envelope response."""
    return FakeResponse({"success": True, "errors": [], "data": data or []})


def _load_hardware_fixture(path=HARDWARE_FIXTURE):
    return json.loads(path.read_text(encoding="utf-8"))


def _response_data(capture, action):
    result = capture["methods"][action]
    assert result["http_status"] == 200
    assert result["response"]["success"] is True
    return result["response"]["data"]


def _feature_map(capture):
    return {
        item["id"]: item["value"] for item in _response_data(capture, "getFeatures")
    }


def _reported_kb(value):
    assert value.endswith(" KB")
    return int(value.removesuffix(" KB").replace(",", ""))


def test_allowlist_contains_only_source_confirmed_read_actions():
    assert {
        "getFeatures": legacy.LegacyAction("GET"),
        "getDiskAllList": legacy.LegacyAction("GET"),
        "getArrayList": legacy.LegacyAction("GET"),
        "getArrayMaintenance": legacy.LegacyAction("POST"),
        "getArrayMiscInfo": legacy.LegacyAction("GET"),
        "getUPSSettings": legacy.LegacyAction("POST"),
        "getOperationStatus": legacy.LegacyAction("POST"),
    } == legacy.READ_ONLY_ACTIONS
    assert "shutRestartLS" not in legacy.READ_ONLY_ACTIONS


@pytest.mark.parametrize(
    "action",
    [
        "shutRestartLS",
        "formatDisk",
        "setArrayMaintenance",
        "setUPSSettings",
        "getUnverifiedAction",
    ],
)
async def test_client_rejects_every_action_outside_allowlist(action):
    session = FakeSession({})
    client = legacy.LegacyCgiClient(
        session,
        "nas.example",
    )

    with pytest.raises(ValueError, match="read-only allowlist"):
        await client.call(action)

    assert session.requests == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "webui_session_admin=temporary_sid_en_admin",
            ("webui_session_admin", "temporary_sid_en_admin"),
        ),
        (
            "webui_session_user-name=value_with_equals=allowed",
            ("webui_session_user-name", "value_with_equals=allowed"),
        ),
    ],
)
def test_temporary_cookie_parsing(value, expected):
    assert legacy.parse_session_cookie(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "sid=value",
        "webui_session_admin",
        "webui_session_admin=",
        "webui_session_admin=value; another=cookie",
        "webui_session_admin=value\r\nInjected: true",
    ],
)
def test_invalid_or_multiple_cookies_are_rejected(value):
    with pytest.raises(ValueError):
        legacy.parse_session_cookie(value)


@pytest.mark.parametrize(
    "host",
    ["https://nas.example", "nas.example/admin", "user@nas.example", "nas\\path"],
)
def test_host_validation_prevents_scheme_downgrade_and_non_endpoint_paths(host):
    with pytest.raises(ValueError):
        legacy._normalize_host(host)


async def test_request_construction_uses_exact_get_and_post_forms():
    session = FakeSession(
        {
            "getFeatures": successful_response([{"id": "SUPPORT_RAID", "value": 1}]),
            "getUPSSettings": successful_response([{"syncUps": "off"}]),
        }
    )
    client = legacy.LegacyCgiClient(
        session,
        "nas.example",
    )
    client.set_session_cookie("webui_session_test=temporary-session")

    await client.call("getFeatures")
    await client.call("getUPSSettings")

    get_method, get_url, get_kwargs = session.requests[0]
    post_method, post_url, post_kwargs = session.requests[1]
    assert (get_method, get_url) == ("GET", "http://nas.example/dynamic.pl")
    assert get_kwargs["params"] == {"bufaction": "getFeatures"}
    assert "data" not in get_kwargs
    assert (post_method, post_url) == ("POST", "http://nas.example/dynamic.pl")
    assert post_kwargs["data"] == {"bufaction": "getUPSSettings"}
    assert "params" not in post_kwargs
    assert get_kwargs["headers"] == {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }
    cookies = session.cookie_jar.filter_cookies(legacy.URL(client.endpoint))
    assert cookies["webui_session_test"].value == "temporary-session"


async def test_native_login_posts_exact_browser_confirmed_form_and_sets_cookie():
    session = FakeSession(
        {
            legacy.LOGIN_ACTION: successful_response(
                [{"sid": "session-secret", "pageMode": "admin"}]
            )
        }
    )
    client = legacy.LegacyCgiClient(session, "nas.example")

    await client.login("admin", "changeme")

    method, url, kwargs = session.requests[0]
    assert (method, url) == ("POST", "http://nas.example/dynamic.pl")
    assert kwargs["data"] == {
        "bufaction": "verifyLogin",
        "user": "admin",
        "password": "changeme",
    }
    assert "params" not in kwargs
    cookies = session.cookie_jar.filter_cookies(legacy.URL(client.endpoint))
    assert cookies["webui_session_admin"].value == "session-secret_en_admin"


async def test_native_login_preserves_cookie_already_set_by_server():
    session = FakeSession(
        {
            legacy.LOGIN_ACTION: successful_response(
                [{"sid": "response-secret", "pageMode": "admin"}]
            )
        }
    )
    client = legacy.LegacyCgiClient(session, "nas.example")
    client.set_session_cookie("webui_session_admin=server-cookie-secret")

    await client.login("admin", "changeme")

    cookies = session.cookie_jar.filter_cookies(legacy.URL(client.endpoint))
    assert cookies["webui_session_admin"].value == "server-cookie-secret"


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "errors": ["invalid_login"], "data": []},
        {"success": True, "errors": [], "data": []},
        {"success": True, "errors": [], "data": [{"pageMode": "admin"}]},
    ],
)
async def test_native_login_rejects_invalid_or_missing_session_data(payload):
    session = FakeSession({legacy.LOGIN_ACTION: FakeResponse(payload)})
    client = legacy.LegacyCgiClient(session, "nas.example")

    with pytest.raises(legacy.LegacyAuthenticationError):
        await client.login("admin", "changeme")

    assert not session.cookie_jar.filter_cookies(legacy.URL(client.endpoint))


async def test_missing_or_expired_cookie_fails_session_validation_cleanly():
    session = FakeSession(
        {
            legacy.VALIDATE_SESSION: FakeResponse(
                {"success": False, "errors": ["expired"], "data": []}
            )
        }
    )
    client = legacy.LegacyCgiClient(session, "nas.example")

    with pytest.raises(legacy.LegacyAuthenticationError):
        await client.validate_session()

    assert len(session.requests) == 1


async def test_native_capture_logs_in_validates_then_reads_without_persisting_secrets(
    tmp_path,
):
    outcomes = {
        legacy.LOGIN_ACTION: successful_response(
            [{"sid": "session-secret", "pageMode": "admin"}]
        ),
        legacy.VALIDATE_SESSION: successful_response(),
        **{
            action: successful_response([{"numeric": 123}])
            for action in legacy.READ_ONLY_ACTIONS
        },
    }
    session = FakeSession(outcomes)
    messages = []

    outcome = await legacy._capture(
        "nas.example",
        10,
        username="admin",
        password="changeme",
        capture_directory=tmp_path,
        reporter=messages.append,
        session_factory=session_factory(session),
    )

    assert outcome.complete is True
    assert [
        (kwargs.get("params") or kwargs.get("data"))["bufaction"]
        for _method, _url, kwargs in session.requests
    ] == [legacy.LOGIN_ACTION, legacy.VALIDATE_SESSION, *legacy.READ_ONLY_ACTIONS]
    capture_text = outcome.capture_path.read_text(encoding="utf-8")
    assert "changeme" not in capture_text
    assert "session-secret" not in capture_text
    assert "admin" not in "\n".join(messages)
    capture = json.loads(capture_text)
    assert capture["authentication"] == {
        "mode": "native_username_password",
        "validated": True,
    }


async def test_native_login_failure_stops_before_validation_and_redacts_secrets(
    tmp_path,
):
    session = FakeSession(
        {
            legacy.LOGIN_ACTION: FakeResponse(
                {"success": False, "errors": ["invalid_login"], "data": []}
            )
        }
    )
    messages = []

    outcome = await legacy._capture(
        "nas.example",
        10,
        username="admin",
        password="changeme",
        capture_directory=tmp_path,
        reporter=messages.append,
        session_factory=session_factory(session),
    )

    assert outcome.complete is False
    assert len(session.requests) == 1
    capture_text = outcome.capture_path.read_text(encoding="utf-8")
    diagnostic_text = outcome.diagnostic_path.read_text(encoding="utf-8")
    assert "changeme" not in capture_text + diagnostic_text
    assert "invalid_login" not in capture_text + diagnostic_text
    assert "admin" not in "\n".join(messages)
    assert json.loads(diagnostic_text)["failed_methods"] == [
        {
            "error_type": "LegacyAuthenticationError",
            "method": legacy.LOGIN_ACTION,
        }
    ]


async def test_authentication_failure_is_recorded_without_other_requests(
    tmp_path,
):
    session = FakeSession(
        {
            legacy.VALIDATE_SESSION: FakeResponse(
                {"success": False, "errors": [], "data": []}
            )
        }
    )

    outcome = await legacy._capture(
        "nas.example",
        10,
        cookie_pair="webui_session_test=temporary-session",
        capture_directory=tmp_path,
        reporter=lambda _message: None,
        session_factory=session_factory(session),
    )

    assert outcome.complete is False
    assert len(session.requests) == 1
    capture = json.loads(outcome.capture_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(outcome.diagnostic_path.read_text(encoding="utf-8"))
    assert capture["methods"] == {}
    assert capture["authentication"]["validated"] is False
    assert diagnostic["failed_methods"] == [
        {
            "error_type": "LegacyAuthenticationError",
            "method": legacy.VALIDATE_SESSION,
        }
    ]
    assert "temporary-session" not in json.dumps(capture)
    assert "temporary-session" not in json.dumps(diagnostic)


async def test_timeout_and_cgi_error_do_not_discard_successful_responses(tmp_path):
    outcomes = {
        action: successful_response([{"action": action}])
        for action in legacy.READ_ONLY_ACTIONS
    }
    outcomes[legacy.VALIDATE_SESSION] = successful_response([{"lang": "en"}])
    outcomes["getDiskAllList"] = TimeoutError()
    outcomes["getArrayList"] = FakeResponse(
        {"success": False, "errors": ["array_temporarily_unavailable"], "data": []}
    )
    session = FakeSession(outcomes)

    outcome = await legacy._capture(
        "nas.example",
        10,
        cookie_pair="webui_session_test=temporary-session",
        capture_directory=tmp_path,
        reporter=lambda _message: None,
        session_factory=session_factory(session),
    )

    assert outcome.complete is False
    capture = json.loads(outcome.capture_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(outcome.diagnostic_path.read_text(encoding="utf-8"))
    assert legacy.VALIDATE_SESSION in capture["methods"]
    assert "getFeatures" in capture["methods"]
    assert "getUPSSettings" in capture["methods"]
    assert "getDiskAllList" not in capture["methods"]
    assert "getArrayList" not in capture["methods"]
    assert diagnostic["failed_methods"] == [
        {
            "error_type": "LegacyTimeoutError",
            "method": "getDiskAllList",
        },
        {
            "cgi_errors": ["array_temporarily_unavailable"],
            "error_type": "LegacyCgiError",
            "method": "getArrayList",
        },
    ]


async def test_response_parser_rejects_non_json_and_incomplete_envelopes():
    invalid_json = FakeResponse(json_error=json.JSONDecodeError("bad", "", 0))
    missing_success = FakeResponse({"errors": [], "data": []})

    with pytest.raises(legacy.LegacyInvalidResponseError):
        await legacy.LegacyCgiClient._decode_response("getFeatures", invalid_json)
    with pytest.raises(legacy.LegacyInvalidResponseError):
        await legacy.LegacyCgiClient._decode_response("getFeatures", missing_success)


def test_sanitizer_redacts_identifiers_and_preserves_technical_values():
    raw = {
        "hostName": "private-nas",
        "serialNumber": "serial-value",
        "sid": "session-value",
        "sessionCookie": "cookie-value",
        "accessToken": "token-value",
        "network": "192.0.2.20 / fe80::1 / 00:11:22:33:44:55",
        "array": {
            "arrayMode": "raid1",
            "arrayStatus": "normal",
            "sizeUsedRaw": 123456789,
            "sizeAllRaw": 987654321,
            "sizeUsedRate": 12.5,
        },
    }

    sanitized = legacy.sanitize(raw, entered_host="private-nas")

    assert sanitized["hostName"] == legacy.REDACTED
    assert sanitized["serialNumber"] == legacy.REDACTED
    assert sanitized["sid"] == legacy.REDACTED
    assert sanitized["sessionCookie"] == legacy.REDACTED
    assert sanitized["accessToken"] == legacy.REDACTED
    assert sanitized["network"] == "<redacted> / <redacted> / <redacted>"
    assert sanitized["array"] == raw["array"]


async def test_complete_capture_has_all_methods_and_never_overwrites(tmp_path):
    outcomes = {
        legacy.VALIDATE_SESSION: successful_response(),
        **{
            action: successful_response([{"numeric": 123}])
            for action in legacy.READ_ONLY_ACTIONS
        },
    }
    first_session = FakeSession(outcomes)

    outcome = await legacy._capture(
        "nas.example",
        10,
        cookie_pair="webui_session_test=temporary-session",
        capture_directory=tmp_path,
        reporter=lambda _message: None,
        session_factory=session_factory(first_session),
    )

    payload = json.loads(outcome.capture_path.read_text(encoding="utf-8"))
    assert outcome.complete is True
    assert outcome.diagnostic_path is None
    assert payload["complete"] is True
    assert payload["authentication"]["mode"] == "manual_temporary_session_cookie"
    assert set(payload["methods"]) == {
        legacy.VALIDATE_SESSION,
        *legacy.READ_ONLY_ACTIONS,
    }
    assert all(result["http_status"] == 200 for result in payload["methods"].values())

    second_session = FakeSession(outcomes)
    with pytest.raises(FileExistsError):
        await legacy._capture(
            "nas.example",
            10,
            cookie_pair="webui_session_test=temporary-session",
            capture_directory=tmp_path,
            reporter=lambda _message: None,
            session_factory=session_factory(second_session),
        )
    assert second_session.requests == []


def test_minimal_hardware_fixture_covers_every_successful_cgi_action():
    capture = _load_hardware_fixture()

    assert capture["complete"] is True
    assert capture["authentication"]["validated"] is True
    assert capture["model"] == "LS-WVL"
    assert capture["firmware"] == "1.75"
    assert set(capture["methods"]) == {
        legacy.VALIDATE_SESSION,
        *legacy.READ_ONLY_ACTIONS,
    }
    for action in capture["methods"]:
        assert isinstance(_response_data(capture, action), list)
        response = capture["methods"][action]["response"]
        assert legacy.sanitize(response) == response


def test_hardware_confirmed_identity_features_and_power_flags():
    features = _feature_map(_load_hardware_fixture())

    assert features["SERIES_NAME"] == "LinkStation"
    assert features["PRODUCT_NAME"] == "LS-WVL(KOUGYOKU)"
    assert features["PRODUCT_CAPACITY"] == "4.0T"
    assert features["SUPPORT_RAID"] == "1"
    assert features["SUPPORT_RAID0"] == "1"
    assert features["SUPPORT_RAID1"] == "1"
    assert features["SUPPORT_SHUTDOWN_FROMWEB"] == "0"
    assert features["SUPPORT_REBOOT_FROMWEB"] == "1"
    assert "FIRMWARE_VERSION" not in features


def test_hardware_confirmed_legacy_storage_shapes_and_reported_kb_unit():
    capture = _load_hardware_fixture()
    disks = _response_data(capture, "getDiskAllList")
    array = _response_data(capture, "getArrayList")[0]
    volume = disks[0]

    used_kb = _reported_kb(volume["amountUsed"])
    total_kb = _reported_kb(volume["capacity"])
    assert used_kb == 1_369_523_620
    assert total_kb == 1_937_364_900
    assert volume["percentUsed"] == "70.69 %"
    assert used_kb / total_kb * 100 == pytest.approx(70.69, abs=0.005)
    assert array["sizeUsedRaw"] == ["1,369,523,620"]
    assert array["sizeAllRaw"] == ["1,937,364,900"]
    assert array["sizeUsedRate"] == ["70.69"]


def test_hardware_confirmed_raid_disk_and_usb_status_values():
    capture = _load_hardware_fixture()
    disks = _response_data(capture, "getDiskAllList")
    array = _response_data(capture, "getArrayList")[0]

    assert array["arrayMode"] == "raid1"
    assert array["arrayStatus"] == "ok"
    assert array["arrayFormat"] == ["xfs"]
    assert array["diskNormal"] == ["disk1", "disk2"]
    assert array["diskError"] is None
    assert [disk["status"] for disk in disks[1:3]] == ["array1", "array1"]
    assert disks[3]["disk"] == "usbdisk1"
    assert disks[3]["status"] == "disconnect"


def test_hardware_confirmed_ups_and_idle_operation_values():
    capture = _load_hardware_fixture()
    ups = _response_data(capture, "getUPSSettings")[0]
    operation = _response_data(capture, "getOperationStatus")[0]

    assert ups == {
        "behavior": "off",
        "connectType": "",
        "net_ipAddr": legacy.REDACTED,
        "recovery": "off",
        "shutdown": "off",
        "syncUps": "off",
        "time": None,
        "upsStatus": "ups_status4",
        "wait": None,
    }
    assert operation == {
        "elapsedTime": None,
        "operation": None,
        "progress": None,
        "task": None,
    }
    assert capture["methods"]["getOperationStatus"]["response"]["errors"] == [None]


def test_real_hardware_capture_matches_regression_shape_when_available():
    if not REAL_CAPTURE.exists():
        pytest.skip("Run the LS-WVL read-only diagnostic first")
    capture = _load_hardware_fixture(REAL_CAPTURE)

    assert capture["complete"] is True
    assert capture["authentication"]["validated"] is True
    assert set(capture["methods"]) == {
        legacy.VALIDATE_SESSION,
        *legacy.READ_ONLY_ACTIONS,
    }
    for result in capture["methods"].values():
        assert legacy.sanitize(result["response"]) == result["response"]
    assert _feature_map(capture)["PRODUCT_NAME"] == "LS-WVL(KOUGYOKU)"
    assert _response_data(capture, "getArrayList")[0]["arrayStatus"] == "ok"
