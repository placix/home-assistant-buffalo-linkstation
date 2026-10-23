"""Tests for read-only hardware capture sanitization and parsing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.buffalo_linkstation.api import (
    BuffaloJsonRpcError,
    extract_identity,
)
from custom_components.buffalo_linkstation.parsing import (
    cpu_status,
    disk_status,
    raid_problem,
    raid_status,
    system_errors,
    system_messages,
    system_problem,
    total_storage,
    used_storage,
    utilization,
)
from tools import capture_readonly
from tools.capture_readonly import (
    READ_METHODS,
    REDACTED,
    _build_capture_from_methods,
    _collect_read_methods,
    build_capture,
    sanitize,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURES = (
    (
        "LS220D",
        "1.86",
        PROJECT_ROOT / "reference/captures/ls220d_1_86.json",
        PROJECT_ROOT / "reference/captures/ls220d_1_86_completed.json",
        {
            "raw_used_kib": 34380,
            "raw_total_kib": 2913320916,
            "total_gib": 2778.4,
            "raid_level": "1",
        },
    ),
    (
        "LS420D",
        "1.88",
        PROJECT_ROOT / "reference/captures/ls420d_1_88.json",
        PROJECT_ROOT / "reference/captures/ls420d_1_88_completed.json",
        {
            "raw_used_kib": 34484,
            "raw_total_kib": 3874730480,
            "total_gib": 3695.2,
            "raid_level": "0",
        },
    ),
)


def test_capture_tool_has_a_fixed_read_only_method_allowlist():
    assert READ_METHODS == (
        "get_portal_info_authneed",
        "getNasFeature",
        "device.getlist",
    )


def test_sanitizer_redacts_identifiers_and_preserves_numbers():
    raw = {
        "hostname": "private-nas",
        "serial_number": "secret-serial",
        "sid": "secret-session",
        "interfaces": [
            {
                "name": "eth0",
                "ipaddr": "192.0.2.10",
                "ethaddr": "00:11:22:33:44:55",
            }
        ],
        "disks": [
            {
                "device_name": "array1",
                "used_size": 1048576,
                "total_size": 4194304,
                "used_percentage": 25.0,
            }
        ],
        "note": "contact owner@example.invalid from 198.51.100.7",
        "remote": "https://private.example/path",
        "ipv6": "fe80::1",
    }

    sanitized = sanitize(raw, entered_host="private-nas")

    assert sanitized["hostname"] == REDACTED
    assert sanitized["serial_number"] == REDACTED
    assert sanitized["sid"] == REDACTED
    assert sanitized["interfaces"][0]["ipaddr"] == REDACTED
    assert sanitized["interfaces"][0]["ethaddr"] == REDACTED
    assert sanitized["note"] == f"contact {REDACTED} from {REDACTED}"
    assert sanitized["remote"] == REDACTED
    assert sanitized["ipv6"] == REDACTED
    assert sanitized["disks"][0]["used_size"] == 1048576
    assert sanitized["disks"][0]["total_size"] == 4194304
    assert sanitized["disks"][0]["used_percentage"] == 25.0


def test_capture_document_keeps_method_result_shapes():
    capture = build_capture(
        "LS220D",
        {"product_name": "LS220D", "hostname": "nas"},
        {"SUPPORT_RAID": 1},
        [{"device_type": "array", "status": ["fine"]}],
        entered_host="nas",
    )

    assert capture["methods"]["get_portal_info_authneed"] == {
        "product_name": "LS220D",
        "hostname": REDACTED,
    }
    assert capture["methods"]["getNasFeature"] == {"SUPPORT_RAID": 1}
    assert capture["methods"]["device.getlist"] == {
        "devices": [{"device_type": "array", "status": ["fine"]}]
    }
    assert capture["complete"] is True


async def test_read_methods_continue_after_json_rpc_failure_and_sanitize_error(capsys):
    calls = []

    class FakeApi:
        async def get_system_info(self):
            calls.append("get_portal_info_authneed")
            return {"hostname": "private-nas", "product_name": "LS220D"}

        async def get_features(self):
            calls.append("getNasFeature")
            raise BuffaloJsonRpcError(
                -32602,
                "Invalid Params from http://private-nas/admin at 192.0.2.10",
            )

        async def get_devices(self):
            calls.append("device.getlist")
            return [{"device_type": "array", "status": ["fine"]}]

    successful, failures = await _collect_read_methods(FakeApi(), "private-nas")

    assert calls == list(READ_METHODS)
    assert set(successful) == {"get_portal_info_authneed", "device.getlist"}
    assert successful["get_portal_info_authneed"]["hostname"] == REDACTED
    assert successful["device.getlist"] == {
        "devices": [{"device_type": "array", "status": ["fine"]}]
    }
    assert failures == [
        {
            "method": "getNasFeature",
            "error_code": -32602,
            "error_type": "json_rpc",
            "message": f"Invalid Params from {REDACTED} at {REDACTED}",
        }
    ]
    output = capsys.readouterr().out
    assert "[INFO] Calling get_portal_info_authneed..." in output
    assert "[INFO] Calling getNasFeature..." in output
    assert "[ERROR] JSON-RPC -32602:" in output
    assert "[INFO] Calling device.getlist..." in output
    assert output.count("[OK] Request successful.") == 2
    assert "private-nas" not in output
    assert "192.0.2.10" not in output


def test_partial_capture_is_explicitly_incomplete():
    capture = _build_capture_from_methods(
        "LS420D",
        {"getNasFeature": {"SUPPORT_RAID": 1}},
        complete=False,
    )

    assert capture["complete"] is False
    assert set(capture["methods"]) == {"getNasFeature"}


async def test_partial_capture_and_diagnostic_are_saved_separately(
    tmp_path, monkeypatch
):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeApi:
        async def login(self):
            return None

        async def get_system_info(self):
            return {"product_name": "LS220D", "fw_version_major": "1.86"}

        async def get_features(self):
            raise BuffaloJsonRpcError(-32603, "Internal Error at private-nas")

        async def get_devices(self):
            return [{"device_type": "array", "status": ["fine"]}]

    fake_api = FakeApi()
    monkeypatch.setattr(capture_readonly, "CAPTURE_DIRECTORY", tmp_path)
    monkeypatch.setattr(capture_readonly, "ClientSession", FakeSession)
    monkeypatch.setattr(
        capture_readonly,
        "BuffaloLinkStationApi",
        lambda *args, **kwargs: fake_api,
    )

    messages = []
    outcome = await capture_readonly._capture(
        "LS220D",
        "private-nas",
        "private-user",
        "private-password",
        None,
        reporter=messages.append,
    )

    assert outcome.complete is False
    assert outcome.diagnostic_path is not None
    capture = json.loads(outcome.capture_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(outcome.diagnostic_path.read_text(encoding="utf-8"))
    assert capture["complete"] is False
    assert set(capture["methods"]) == {
        "get_portal_info_authneed",
        "device.getlist",
    }
    assert diagnostic["capture_status"] == "partial"
    assert diagnostic["failed_methods"] == [
        {
            "error_code": -32603,
            "error_type": "json_rpc",
            "message": f"Internal Error at {REDACTED}",
            "method": "getNasFeature",
        }
    ]
    assert messages[0] == "[INFO] Authenticating..."
    assert messages[1] == "[OK] Authentication successful."
    assert "private-nas" not in json.dumps(diagnostic)
    assert "private-user" not in json.dumps(diagnostic)
    assert "private-password" not in json.dumps(diagnostic)


async def test_feature_retry_preserves_partial_and_writes_completed_copy(
    tmp_path, monkeypatch
):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeApi:
        def __init__(self, session, host, username, password, **kwargs):
            assert username == ""
            assert password == ""

        async def get_system_info(self):
            raise AssertionError("Portal information must not be refreshed")

        async def get_features(self):
            return {"SUPPORT_RAID": 1}

        async def get_devices(self):
            raise AssertionError("Devices must not be refreshed")

    monkeypatch.setattr(capture_readonly, "CAPTURE_DIRECTORY", tmp_path)
    monkeypatch.setattr(capture_readonly, "ClientSession", FakeSession)
    monkeypatch.setattr(capture_readonly, "BuffaloLinkStationApi", FakeApi)
    source_path = tmp_path / capture_readonly.OUTPUT_NAMES["LS220D"]
    source = _build_capture_from_methods(
        "LS220D",
        {
            "get_portal_info_authneed": {
                "product_name": "LS220D",
                "fw_version_major": "1.86",
            },
            "device.getlist": {"devices": []},
        },
        complete=False,
    )
    source_text = json.dumps(source, indent=2, sort_keys=True) + "\n"
    source_path.write_text(source_text, encoding="utf-8")

    completed_path = await capture_readonly._retry_feature_capture(
        "LS220D",
        "private-nas",
        None,
        reporter=lambda _message: None,
    )

    assert completed_path is not None
    assert source_path.read_text(encoding="utf-8") == source_text
    completed = json.loads(completed_path.read_text(encoding="utf-8"))
    assert completed["complete"] is True
    assert set(completed["methods"]) == set(READ_METHODS)
    assert completed["methods"]["getNasFeature"] == {"SUPPORT_RAID": 1}


@pytest.mark.parametrize(
    ("model", "firmware", "partial_path", "completed_path", "expected"), CAPTURES
)
def test_real_sanitized_capture_parses_when_available(
    model, firmware, partial_path, completed_path, expected
):
    """Validate ignored hardware fixtures after the operator captures them."""
    capture_path = completed_path if completed_path.exists() else partial_path
    if not capture_path.exists():
        pytest.skip(f"Run the read-only capture tool for {model} first")
    payload = json.loads(capture_path.read_text(encoding="utf-8"))
    if payload.get("complete") is not True:
        pytest.skip(f"Retry the failed feature capture for {model} first")

    assert payload["model"] == model
    assert payload["firmware"] == firmware
    assert payload["complete"] is True
    assert sanitize(payload) == payload

    methods = payload["methods"]
    assert set(methods) == set(READ_METHODS)
    portal = methods["get_portal_info_authneed"]
    features = methods["getNasFeature"]
    devices = methods["device.getlist"]["devices"]
    assert isinstance(portal, dict)
    assert isinstance(features, dict)
    assert isinstance(devices, list)
    assert len(devices) == 6
    assert features["SUPPORT_RAID"] == 1
    assert features["SUPPORT_SHUTDOWN_FROMWEB"] == 0
    assert features["SUPPORT_REBOOT_FROMWEB"] == 1

    identity = extract_identity(portal, REDACTED)
    assert identity.model == model
    assert identity.firmware == firmware

    data = SimpleNamespace(
        system_info=portal,
        features=features,
        devices=tuple(devices),
        identity=identity,
    )
    disks = portal.get("disks")
    assert isinstance(disks, list)
    assert len(disks) == 1
    assert disks[0]["used_size"] == expected["raw_used_kib"]
    assert disks[0]["total_size"] == expected["raw_total_kib"]
    assert disks[0]["used_percentage"] == 0.0

    arrays = [device for device in devices if device.get("device_type") == "array"]
    assert len(arrays) == 1
    assert arrays[0]["raid_level"] == expected["raid_level"]
    assert arrays[0]["status"] == ["fine"]
    assert cpu_status(data) == "normal_state"
    assert system_errors(data) is None
    assert system_messages(data) is None
    assert system_problem(data) is False
    assert disk_status(data) == "fine"
    assert raid_status(data) == "fine"
    assert raid_problem(data) is False
    assert used_storage(data) == 0.0
    assert total_storage(data) == expected["total_gib"]
    assert utilization(data) == 0.0
