"""Tests for conservative sensor and feature parsing."""

from types import SimpleNamespace

from custom_components.buffalo_linkstation import parsing
from custom_components.buffalo_linkstation.api import BuffaloIdentity


def snapshot(system_info=None, devices=()):
    return SimpleNamespace(
        system_info=system_info or {},
        features={},
        devices=tuple(devices),
        identity=BuffaloIdentity(model="LS220D"),
    )


def test_portal_storage_fields_use_confirmed_kib_units():
    data = snapshot(
        {
            "cpu_status": "normal_state",
            "errors": [{"code": "E30"}],
            "informations": [{"message": "RAID check scheduled"}],
            "disks": [
                {
                    "device_name": "array1",
                    "used_size": 1048576,
                    "total_size": 4194304,
                    "used_percentage": 25,
                }
            ],
        }
    )
    assert parsing.cpu_status(data) == "normal_state"
    assert parsing.system_errors(data) == "E30"
    assert parsing.system_messages(data) == "RAID check scheduled"
    assert parsing.used_storage(data) == 1.0
    assert parsing.total_storage(data) == 4.0
    assert parsing.utilization(data) == 25.0


def test_missing_or_invalid_storage_data():
    assert parsing.utilization(snapshot({"disks": []})) is None
    assert (
        parsing.utilization(snapshot({"disks": [{"total_size": 0, "used_size": 0}]}))
        is None
    )
    assert parsing.used_storage(snapshot({"disks": [{"total_size": "bad"}]})) is None


def test_missing_health_fields_do_not_invent_entity_values():
    data = snapshot(devices=[{"device_type": "disk", "interface": "usb"}])
    assert parsing.cpu_status(data) is None
    assert parsing.system_errors(data) is None
    assert parsing.system_messages(data) is None
    assert parsing.system_problem(data) is None
    assert parsing.disk_status(data) is None
    assert parsing.raid_status(data) is None
    assert parsing.raid_problem(data) is None


def test_problem_states_are_conservative():
    assert parsing.system_problem(snapshot({"errors": [{"code": "E30"}]})) is True
    assert parsing.system_problem(snapshot({"errors": []})) is False
    assert parsing.system_problem(snapshot({})) is None

    healthy = [{"device_type": "array", "status": ["fine"]}]
    degraded = [{"device_type": "array", "status": ["fine", "degrade"]}]
    rebuilding = [{"device_type": "array", "status": ["rebuild"]}]
    assert parsing.raid_problem(snapshot(devices=degraded)) is True
    assert parsing.raid_problem(snapshot(devices=healthy)) is False
    assert parsing.raid_problem(snapshot(devices=rebuilding)) is False
    assert parsing.raid_status(snapshot(devices=degraded)) == "degrade"


def test_device_status_uses_firmware_priority_order():
    devices = [
        {"device_type": "disk", "status": ["fine", "checking"]},
        {"device_type": "array", "status": ["resync", "fine"]},
    ]
    data = snapshot(devices=devices)
    assert parsing.disk_status(data) == "checking"
    assert parsing.raid_status(data) == "resync"


def test_disk_status_excludes_disconnected_usb_placeholder():
    devices = [
        {"device_type": "disk", "interface": "sata", "status": ["fine"]},
        {"device_type": "disk", "interface": "usb", "status": ["disconnected"]},
    ]
    assert parsing.disk_status(snapshot(devices=devices)) == "fine"


def test_feature_and_recursive_value_parsing():
    features = {"features": {"SUPPORT_REBOOT_FROMWEB": "1"}}
    assert parsing.feature_enabled(features, "SUPPORT_REBOOT_FROMWEB")
    assert not parsing.feature_enabled(features, "SUPPORT_SHUTDOWN_FROMWEB")
    assert parsing.find_value({"a": [{"model": "LS420D"}]}, "model") == "LS420D"
