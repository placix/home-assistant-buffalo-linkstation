"""Tests for the privacy-first HAR compatibility diagnostic."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tools.sanitize_har import (
    DEFAULT_OUTPUT_NAME,
    HarSanitizationError,
    load_har,
    main,
    sanitize_har_document,
)


def _entry(
    *,
    url: str,
    method: str = "POST",
    post_data: dict | None = None,
    query: list[dict] | None = None,
    response_text: str | None = None,
    response_encoding: str | None = None,
    response_mime: str = "application/json",
    status: int = 200,
) -> dict:
    request = {
        "method": method,
        "url": url,
        "headers": [],
        "cookies": [],
        "queryString": query or [],
    }
    if post_data is not None:
        request["postData"] = post_data
    content: dict = {"mimeType": response_mime}
    if response_text is not None:
        content["text"] = response_text
    if response_encoding is not None:
        content["encoding"] = response_encoding
    return {
        "startedDateTime": "2026-01-01T00:00:00Z",
        "time": 1,
        "request": request,
        "response": {
            "status": status,
            "statusText": "secret error detail",
            "headers": [],
            "cookies": [],
            "content": content,
        },
    }


def _har(*entries: dict) -> dict:
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "Private Browser", "version": "1"},
            "pages": [{"title": "Private NAS"}],
            "entries": list(entries),
        }
    }


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def test_json_rpc_removes_numeric_telemetry_recursively():
    private_address = "10.23.45.67"  # privacy-check: allow
    secrets = {
        "password": "synthetic-password",  # privacy-check: allow
        "sid": "synthetic-session",
        "hostname": "private-nas.local",
        "serialNumber": "SYNTHETIC-SERIAL-123",
        "macAddress": "02:00:00:12:34:56",
        "email": "owner@example.invalid",
        "share": r"\\private-nas\finance\payroll.xlsx",
    }
    request_body = {
        "jsonrpc": "2.0",
        "method": "device.getlist",
        "params": {
            "sid": secrets["sid"],
            "detail": True,
            "limit": 25,
            "paging": {"offset": 5},
            "indexes": [7, 8],
        },
        "id": "request-secret",
    }
    credential_url = "http://admin:secret@"  # privacy-check: allow
    response_body = {
        "jsonrpc": "2.0",
        "result": {
            **secrets,
            "capacity": 123456789,
            "percentUsed": 42.5,
            "total_size": 2913320916,
            "used_size": 34380,
            "used_percentage": 0.0,
            "temperature": 39,
            "nested": {"voltage": 12.5, "code": 700},
            "samples": [10, 20.5, {"capacity": 30}],
            "status": "ok",
            "description": "confidential free-form message",
        },
        "id": "private-response-id",
    }
    entry = _entry(
        url=f"{credential_url}{private_address}/nasapi/?sid={secrets['sid']}",
        post_data={
            "mimeType": "application/json",
            "text": json.dumps(request_body),
        },
        query=[{"name": "sid", "value": secrets["sid"]}],
        response_text=json.dumps(response_body),
    )
    entry["request"]["headers"] = [
        {"name": "Authorization", "value": "Basic synthetic-authorization"},
        {"name": "Cookie", "value": "sid=synthetic-cookie"},
    ]
    entry["request"]["cookies"] = [
        {"name": "webui_session_admin", "value": "synthetic-cookie"}
    ]

    diagnostic = sanitize_har_document(_har(entry), model="LS520D", firmware="4.00")
    serialized = _dump(diagnostic)

    for secret in (
        *secrets.values(),
        private_address,
        "admin",
        "secret",
        "request-secret",
        "synthetic-authorization",
        "synthetic-cookie",
        "confidential free-form message",
        "private-response-id",
    ):
        assert secret not in serialized
    transaction = diagnostic["transactions"][0]
    assert transaction["protocol"] == "modern_json_rpc"
    assert transaction["api_method"] == "device.getlist"
    assert transaction["endpoint_path"] == "/nasapi/"
    assert transaction["http_status"] == 200
    params = transaction["parameters"]["fields"]
    assert params["sid"] == {"type": "string"}
    assert params["limit"] == {"type": "integer"}
    assert params["paging"]["fields"]["offset"] == {"type": "integer"}
    assert params["indexes"]["items"] == [
        {"type": "integer"},
        {"type": "integer"},
    ]
    assert transaction["query_parameters"] == [
        {"name": "sid", "type": "string", "sensitive": True}
    ]
    response_fields = transaction["response_structure"]["fields"]
    result_fields = response_fields["result"]["fields"]
    assert result_fields["capacity"] == {"type": "integer"}
    assert result_fields["percentUsed"] == {"type": "number"}
    assert result_fields["total_size"] == {"type": "integer"}
    assert result_fields["used_size"] == {"type": "integer"}
    assert result_fields["used_percentage"] == {"type": "number"}
    assert result_fields["temperature"] == {"type": "integer"}
    assert result_fields["nested"]["fields"] == {
        "voltage": {"type": "number"},
        "code": {"type": "integer"},
    }
    assert result_fields["samples"]["items"] == [
        {"type": "integer"},
        {"type": "number"},
        {
            "type": "object",
            "fields": {"capacity": {"type": "integer"}},
        },
    ]
    assert result_fields["status"]["value"] == "ok"
    assert diagnostic["user_supplied"] == {
        "device_model": "LS520D",
        "firmware_version": "4.00",
    }


@pytest.mark.parametrize("error_code", [-119, -32601])
def test_confirmed_json_rpc_error_codes_are_preserved(error_code):
    entry = _entry(
        url="http://nas.invalid/nasapi/",
        post_data={
            "text": json.dumps(
                {"jsonrpc": "2.0", "method": "device.getlist", "params": {}}
            )
        },
        response_text=json.dumps(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": error_code,
                    "message": "private server error detail",
                    "data": {"code": 999, "retry_after": 30},
                },
                "id": "private-response-id",
            }
        ),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]
    response_fields = transaction["response_structure"]["fields"]
    error_fields = response_fields["error"]["fields"]

    assert error_fields["code"] == {"type": "integer", "value": error_code}
    assert error_fields["data"]["fields"] == {
        "code": {"type": "integer"},
        "retry_after": {"type": "integer"},
    }
    assert "private server error detail" not in _dump(transaction)
    assert "private-response-id" not in _dump(transaction)


@pytest.mark.parametrize(
    "response_body",
    [
        {"jsonrpc": "2.0", "result": {"code": 123}},
        {"jsonrpc": "2.0", "error": {"code": -119}},
        {"error": {"code": -32601, "message": "private"}},
    ],
)
def test_arbitrary_code_fields_do_not_bypass_numeric_sanitization(response_body):
    entry = _entry(
        url="http://nas.invalid/nasapi/",
        post_data={
            "text": json.dumps(
                {"jsonrpc": "2.0", "method": "device.getlist", "params": {}}
            )
        },
        response_text=json.dumps(response_body),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]
    serialized = _dump(transaction["response_structure"])

    assert '"code": {"type": "integer"}' in serialized
    assert '"code": {"type": "integer", "value":' not in serialized


def test_json_rpc_authentication_and_power_responses_are_omitted():
    entries = [
        _entry(
            url="http://nas.invalid/nasapi/",
            post_data={
                "mimeType": "application/json",
                "text": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": method,
                        "params": {"password": "synthetic-password"},
                        "id": "private-id",
                    }
                ),
            },
            response_text=json.dumps(
                {"result": {"sid": "synthetic-session", "message": "private"}}
            ),
        )
        for method in ("Auth.login", "System.shutdown", "System.reboot")
    ]

    diagnostic = sanitize_har_document(_har(*entries))

    for transaction in diagnostic["transactions"]:
        assert transaction["response_structure"] == {
            "type": "omitted",
            "reason": "authentication_or_sensitive_operation",
        }
    serialized = _dump(diagnostic)
    assert "synthetic-password" not in serialized
    assert "synthetic-session" not in serialized
    assert "private-id" not in serialized
    assert "Auth.login" not in serialized
    assert "System.shutdown" not in serialized
    assert "System.reboot" not in serialized
    assert {item["api_method"] for item in diagnostic["transactions"]} == {
        "<authentication-operation>",
        "<sensitive-operation>",
    }


def test_cgi_form_records_action_and_types_without_credentials():
    form_text = (
        "bufaction=verifyLogin&user=synthetic-user&"
        "password=synthetic-password&sid=synthetic-session"
    )
    entry = _entry(
        url="http://legacy-nas.invalid/dynamic.pl",
        post_data={
            "mimeType": "application/x-www-form-urlencoded",
            "text": form_text,
        },
        response_text=json.dumps(
            {"success": True, "data": [{"sid": "synthetic-session"}]}
        ),
    )

    diagnostic = sanitize_har_document(_har(entry))
    transaction = diagnostic["transactions"][0]
    serialized = _dump(transaction)

    assert transaction["protocol"] == "legacy_cgi"
    assert transaction["cgi_action"] == "<authentication-operation>"
    assert transaction["operation_category"] == "authentication"
    assert transaction["known_operation"] is True
    assert "parameters" not in transaction
    assert transaction["response_structure"]["type"] == "omitted"
    assert "synthetic-user" not in serialized
    assert "synthetic-password" not in serialized
    assert "synthetic-session" not in serialized


def test_cgi_power_action_is_minimal_and_never_executed():
    entry = _entry(
        url="http://legacy-nas.invalid/dynamic.pl",
        post_data={
            "params": [
                {"name": "bufaction", "value": "shutRestartLS"},
                {"name": "action", "value": "shutdown"},
                {"name": "sid", "value": "synthetic-session"},
            ]
        },
        response_text=json.dumps({"success": True, "message": "private"}),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]

    assert transaction["cgi_action"] == "<sensitive-operation>"
    assert transaction["operation_category"] == "state_changing"
    assert "cgi_suboperation" not in transaction
    assert "parameters" not in transaction
    assert transaction["response_structure"]["type"] == "omitted"


def test_unknown_endpoint_is_reduced_to_counts_and_no_paths_or_values():
    private_address = "192.168.77.3"  # privacy-check: allow
    filename = "family-photos.zip"
    entry = _entry(
        url=f"http://{private_address}/private/{filename}?token=synthetic-token",
        post_data={"text": "share=finance&filename=payroll.xlsx"},
        query=[{"name": "token", "value": "synthetic-token"}],
        response_text="owner@example.invalid has access to finance",
    )

    diagnostic = sanitize_har_document(
        _har(entry),
        model=f"LS520D at {private_address}",
        firmware="private-nas.local",
    )
    transaction = diagnostic["transactions"][0]
    serialized = _dump(transaction)

    assert transaction["protocol"] == "unknown"
    assert transaction["endpoint_path"] == "<unknown-endpoint>"
    assert transaction["safe_metadata"] == {
        "query_parameter_count": 1,
        "has_request_body": True,
        "has_response_body": True,
    }
    assert diagnostic["user_supplied"] == {
        "device_model": "<redacted>",
        "firmware_version": "<redacted>",
    }
    for secret in (
        private_address,
        filename,
        "synthetic-token",
        "finance",
        "payroll.xlsx",
        "owner@example.invalid",
    ):
        assert secret not in serialized


def test_nested_json_escaped_and_base64_secrets_are_generalized():
    secret = "escaped-synthetic-secret"
    response = {
        "result": {
            "nested": [{"message": secret, "capacity": 987654321}],
            "networkPath": r"\\nas\private\document.pdf",
            "shares": {"PrivateFinance": {"filename": "payroll.xlsx"}},
        }
    }
    escaped_json = json.dumps(response).replace("escaped", r"\u0065scaped")
    encoded = base64.b64encode(escaped_json.encode()).decode()
    entry = _entry(
        url="http://nas.invalid/nasapi/",
        post_data={
            "text": json.dumps(
                {"jsonrpc": "2.0", "method": "device.getlist", "params": {}}
            )
        },
        response_text=encoded,
        response_encoding="base64",
    )

    diagnostic = sanitize_har_document(_har(entry))
    serialized = _dump(diagnostic)

    assert secret not in serialized
    assert "document.pdf" not in serialized
    assert "PrivateFinance" not in serialized
    assert "payroll.xlsx" not in serialized
    nested = diagnostic["transactions"][0]["response_structure"]["fields"]["result"][
        "fields"
    ]["nested"]["items"][0]["fields"]
    assert nested["message"] == {"type": "string"}
    assert nested["capacity"] == {"type": "integer"}
    shares = diagnostic["transactions"][0]["response_structure"]["fields"]["result"][
        "fields"
    ]["shares"]
    assert shares == {
        "type": "object",
        "field_count": 1,
        "contents": "omitted_sensitive_container",
    }


def test_feature_flag_response_is_bounded_and_reports_type_counts():
    features = {f"SUPPORT_SYNTHETIC_{index:03d}": index % 2 for index in range(80)}
    entry = _entry(
        url="http://nas.invalid/nasapi/",
        post_data={
            "text": json.dumps(
                {"jsonrpc": "2.0", "method": "getNasFeature", "params": {}}
            )
        },
        response_text=json.dumps(
            {"jsonrpc": "2.0", "result": features, "id": "private-id"}
        ),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]
    feature_schema = transaction["response_structure"]["fields"]["result"]

    assert feature_schema["profile"] == "feature_flag_map"
    assert feature_schema["field_count"] == 80
    assert len(feature_schema["field_names"]) == 32
    assert feature_schema["field_names_truncated"] is True
    assert feature_schema["value_type_counts"] == {"integer": 80}
    assert "private-id" not in _dump(transaction)


def test_legacy_get_features_uses_one_schema_for_147_identical_entries():
    private_values = [f"private-feature-value-{index}" for index in range(147)]
    features = [
        {
            "id": f"FEATURE_{index:03d}",
            "value": private_values[index],
            "capacity": 900_000 + index,
            "status": "ok",
        }
        for index in range(147)
    ]
    entry = _entry(
        url="http://legacy-nas.invalid/dynamic.pl",
        method="GET",
        query=[{"name": "bufaction", "value": "getFeatures"}],
        response_text=json.dumps({"success": True, "data": features}),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]
    data_schema = transaction["response_structure"]["fields"]["data"]
    serialized = _dump(transaction)

    assert data_schema == {
        "type": "array",
        "length": 147,
        "item_schema": {
            "type": "object",
            "fields": {
                "capacity": {"type": "integer"},
                "id": {"type": "string"},
                "status": {"type": "string"},
                "value": {"type": "string"},
            },
        },
    }
    assert "items" not in data_schema
    assert all(value not in serialized for value in private_values)
    assert json.loads(serialized) == transaction


def test_legacy_get_features_handles_an_empty_feature_array():
    entry = _entry(
        url="http://legacy-nas.invalid/dynamic.pl",
        method="GET",
        query=[{"name": "bufaction", "value": "getFeatures"}],
        response_text=json.dumps({"success": True, "data": []}),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]

    assert transaction["response_structure"]["fields"]["data"] == {
        "type": "array",
        "length": 0,
    }


@pytest.mark.parametrize(
    ("payload", "expected_data_type"),
    [
        ({"success": True}, None),
        ({"success": True, "data": None}, "null"),
        ({"success": True, "data": "malformed-private-value"}, "string"),
    ],
)
def test_legacy_get_features_handles_missing_null_and_malformed_data(
    payload, expected_data_type
):
    entry = _entry(
        url="http://legacy-nas.invalid/dynamic.pl",
        method="GET",
        query=[{"name": "bufaction", "value": "getFeatures"}],
        response_text=json.dumps(payload),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]
    fields = transaction["response_structure"]["fields"]

    if expected_data_type is None:
        assert "data" not in fields
    else:
        assert fields["data"] == {"type": expected_data_type}
    assert "malformed-private-value" not in _dump(transaction)


def test_legacy_get_features_mixed_schemas_are_deterministic_and_bounded():
    features = [
        {
            "id": f"FEATURE_{index:02d}",
            "value": f"private-value-{index}",
            f"variant_{index:02d}": index,
        }
        for index in range(12)
    ]

    def data_schema(items):
        entry = _entry(
            url="http://legacy-nas.invalid/dynamic.pl",
            method="GET",
            query=[{"name": "bufaction", "value": "getFeatures"}],
            response_text=json.dumps({"success": True, "data": items}),
        )
        transaction = sanitize_har_document(_har(entry))["transactions"][0]
        return transaction["response_structure"]["fields"]["data"], transaction

    forward_schema, transaction = data_schema(features)
    reverse_schema, _ = data_schema(list(reversed(features)))

    assert forward_schema == reverse_schema
    assert forward_schema["length"] == 12
    assert forward_schema["schema_variant_count"] == 12
    assert len(forward_schema["item_schema_variants"]) == 8
    assert forward_schema["schema_variants_truncated"] is True
    assert all(
        variant["count"] == 1 for variant in forward_schema["item_schema_variants"]
    )
    assert all(feature["value"] not in _dump(transaction) for feature in features)


def test_other_legacy_cgi_arrays_keep_the_existing_item_descriptions():
    entry = _entry(
        url="http://legacy-nas.invalid/dynamic.pl",
        method="GET",
        query=[{"name": "bufaction", "value": "getDiskAllList"}],
        response_text=json.dumps(
            {
                "success": True,
                "data": [
                    {"status": "normal", "capacity": 1000},
                    {"status": "normal", "capacity": 2000},
                ],
            }
        ),
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]
    data_schema = transaction["response_structure"]["fields"]["data"]

    assert len(data_schema["items"]) == 2
    assert "item_schema" not in data_schema
    assert data_schema["items"][0]["fields"] == {
        "status": {"type": "string", "value": "normal"},
        "capacity": {"type": "integer"},
    }


def test_default_output_skips_static_unknown_web_and_unconfirmed_api_methods():
    unconfirmed_method = "getPrivateExperimentalMethod"
    entries = [
        _entry(
            url="http://nas.invalid/js/application.js",
            method="GET",
            response_text="private static content",
        ),
        _entry(
            url="http://nas.invalid/dashboard",
            method="GET",
            response_text="private page content",
        ),
        _entry(
            url="http://nas.invalid/custom-api",
            post_data={"text": json.dumps({"private": "value"})},
            response_text=json.dumps({"private": "value"}),
        ),
        _entry(
            url="http://nas.invalid/nasapi/",
            post_data={
                "text": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": unconfirmed_method,
                        "params": {"secret": "private"},
                    }
                )
            },
            response_text=json.dumps({"result": {"secret": "private"}}),
        ),
        _entry(
            url="http://nas.invalid/nasapi/",
            post_data={
                "text": json.dumps(
                    {"jsonrpc": "2.0", "method": "get_portal_info", "params": {}}
                )
            },
            response_text=json.dumps({"result": {"status": "ok"}}),
        ),
    ]
    entries[0]["response"]["content"]["mimeType"] = "application/javascript"
    entries[1]["response"]["content"]["mimeType"] = "text/plain"

    diagnostic = sanitize_har_document(_har(*entries))
    serialized = _dump(diagnostic)

    assert len(diagnostic["transactions"]) == 1
    assert diagnostic["transactions"][0]["api_method"] == "get_portal_info"
    assert diagnostic["protocol_evidence"] == ["modern_json_rpc"]
    assert diagnostic["summary"]["detected_protocol_entries"] == {"modern_json_rpc": 2}
    assert diagnostic["summary"]["skipped_entry_count"] == 4
    assert diagnostic["summary"]["skipped_entries_by_category"] == {
        "static_resource": 1,
        "unknown_protocol_candidate": 1,
        "unknown_web_request": 1,
        "unrecognized_json_rpc_method": 1,
    }
    assert unconfirmed_method not in serialized
    assert "private static content" not in serialized
    assert "private page content" not in serialized


def test_real_hardware_scale_capture_keeps_only_confirmed_api_transactions():
    static_entries = [
        _entry(
            url=f"http://nas.invalid/static/resource-{index}.js",
            method="GET",
            response_text="synthetic static content",
            response_mime="application/javascript",
        )
        for index in range(300)
    ]
    web_entries = [
        _entry(
            url=f"http://nas.invalid/page-{index}",
            method="GET",
            response_text="synthetic web content",
            response_mime="text/plain",
        )
        for index in range(150)
    ]
    unconfirmed_api_entries = [
        _entry(
            url="http://nas.invalid/nasapi/",
            post_data={
                "text": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": f"getUnconfirmedSynthetic{index}",
                        "params": {},
                    }
                )
            },
            response_text=json.dumps({"result": {"status": "ok"}}),
        )
        for index in range(19)
    ]
    confirmed_api_entries = [
        _entry(
            url="http://nas.invalid/nasapi/",
            post_data={
                "text": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "get_portal_info_authneed",
                        "params": {},
                    }
                )
            },
            response_text=json.dumps({"result": {"status": "ok"}}),
        )
        for _ in range(8)
    ]

    diagnostic = sanitize_har_document(
        _har(
            *static_entries,
            *web_entries,
            *unconfirmed_api_entries,
            *confirmed_api_entries,
        )
    )

    assert diagnostic["summary"] == {
        "har_entry_count": 477,
        "included_transaction_count": 8,
        "skipped_entry_count": 469,
        "skipped_entries_by_category": {
            "static_resource": 300,
            "unknown_web_request": 150,
            "unrecognized_json_rpc_method": 19,
        },
        "detected_protocol_entries": {"modern_json_rpc": 27},
        "unknown_protocol_samples_included": 0,
        "malformed_entry_count": 0,
        "anonymous_device_count": 1,
    }
    assert len(diagnostic["transactions"]) == 8
    assert {
        transaction["api_method"] for transaction in diagnostic["transactions"]
    } == {"get_portal_info_authneed"}


def test_missing_response_body_is_recorded_without_failure():
    entry = _entry(
        url="http://nas.invalid/dynamic.pl",
        method="GET",
        query=[{"name": "bufaction", "value": "getFeatures"}],
    )

    transaction = sanitize_har_document(_har(entry))["transactions"][0]

    assert transaction["cgi_action"] == "getFeatures"
    assert transaction["response_structure"] == {"type": "missing"}


@pytest.mark.parametrize(
    "invalid",
    [None, [], {}, {"log": {}}, {"log": {"entries": "not-a-list"}}],
)
def test_malformed_har_structure_fails_closed(invalid):
    with pytest.raises(HarSanitizationError, match="valid HAR"):
        sanitize_har_document(invalid)


def test_malformed_har_json_reports_only_safe_error(tmp_path: Path):
    path = tmp_path / "input.har"
    path.write_text('{"private": "synthetic-secret"', encoding="utf-8")

    with pytest.raises(HarSanitizationError) as error:
        load_har(path)

    assert "synthetic-secret" not in str(error.value)


def test_cli_writes_valid_diagnostic_and_never_overwrites(tmp_path: Path, capsys):
    input_path = tmp_path / "input.har"
    input_path.write_text(
        json.dumps(
            _har(
                _entry(
                    url="http://nas.invalid/nasapi/",
                    post_data={
                        "text": json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": "getNasFeature",
                                "params": {},
                            }
                        )
                    },
                    response_text=json.dumps({"result": {"status": "ok"}}),
                )
            )
        ),
        encoding="utf-8",
    )

    assert main([str(input_path), "--model", "LS720D", "--firmware", "1.00"]) == 0
    output = input_path.with_name(DEFAULT_OUTPUT_NAME)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["format"] == "buffalo_compatibility_diagnostic"
    assert result["format_version"] == 2
    assert result["privacy"]["review_required_before_sharing"] is True
    assert result["user_supplied"]["device_model"] == "LS720D"
    assert main([str(input_path)]) == 2
    captured = capsys.readouterr()
    assert "Inspect the generated JSON" in captured.out
    assert "already exists" in captured.err
