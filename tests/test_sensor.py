"""Tests for protocol-neutral monitoring entities."""

from datetime import timedelta
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buffalo_linkstation.api import BuffaloIdentity
from custom_components.buffalo_linkstation.const import DOMAIN
from custom_components.buffalo_linkstation.coordinator import (
    BuffaloLinkStationCoordinator,
    BuffaloLinkStationData,
)
from custom_components.buffalo_linkstation.sensor import (
    SENSORS,
    BuffaloLinkStationSensor,
    async_setup_entry,
)


def legacy_coordinator(hass, entry):
    api = MagicMock(host="legacy.local", endpoint="http://legacy.local/dynamic.pl")
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))
    coordinator.async_set_updated_data(
        BuffaloLinkStationData(
            system_info={
                "product_name": "LS-WVL(KOUGYOKU)",
                "storage": {
                    "used": 1_369_523_620,
                    "total": 1_937_364_900,
                    "unit": "vendor_reported_kb",
                    "utilization": 70.69,
                },
            },
            features={},
            devices=(
                {
                    "device_type": "array",
                    "name": "array1",
                    "status": "ok",
                    "raid_level": "raid1",
                    "members": ("disk1", "disk2"),
                },
                {
                    "device_type": "disk",
                    "name": "disk1",
                    "interface": "sata",
                    "status": "ok",
                    "array": "array1",
                    "member_state": "ok",
                },
                {
                    "device_type": "disk",
                    "name": "usbdisk1",
                    "interface": "usb",
                    "status": "disconnect",
                },
            ),
            identity=BuffaloIdentity(
                hostname="legacy.local",
                model="LS-WVL(KOUGYOKU)",
            ),
        )
    )
    return coordinator


async def test_legacy_entities_use_reported_units_and_omit_unavailable_fields(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="host:legacy.local",
    )
    coordinator = legacy_coordinator(hass, entry)
    entry.runtime_data = MagicMock(coordinator=coordinator)
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    entities = list(add_entities.call_args.args[0])
    by_key = {entity.entity_description.key: entity for entity in entities}
    assert "firmware_version" not in by_key
    assert "cpu_status" not in by_key
    assert "system_errors" not in by_key
    assert "system_messages" not in by_key
    assert by_key["storage_used"].native_value == 1_369_523_620
    assert by_key["storage_total"].native_value == 1_937_364_900
    assert by_key["storage_used"].native_unit_of_measurement == "KB (reported)"
    assert by_key["storage_utilization"].native_value == 70.7
    assert by_key["disk_status"].native_value == "ok"
    assert by_key["raid_status"].native_value == "ok"
    assert by_key["raid_mode"].native_value == "raid1"
    assert by_key["disk_status"].extra_state_attributes == {
        "disks": [
            {
                "name": "disk1",
                "status": "ok",
                "array": "array1",
                "member_state": "ok",
            }
        ]
    }
    assert by_key["raid_mode"].unique_id == "host:legacy.local_raid_mode"


def test_modern_storage_entity_keeps_existing_gib_unit_and_unique_id(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="serial:modern")
    api = MagicMock(host="modern.local", endpoint="http://modern.local/nasapi/")
    coordinator = BuffaloLinkStationCoordinator(hass, entry, api, timedelta(seconds=60))
    coordinator.async_set_updated_data(
        BuffaloLinkStationData(
            system_info={"disks": [{"used_size": 1_048_576, "total_size": 4_194_304}]},
            features={},
            devices=(),
            identity=BuffaloIdentity(model="LS220D", firmware="1.86"),
        )
    )
    description = next(item for item in SENSORS if item.key == "storage_used")
    entity = BuffaloLinkStationSensor(entry, coordinator, description)

    assert entity.native_value == 1.0
    assert entity.native_unit_of_measurement == "GiB"
    assert entity.unique_id == "serial:modern_storage_used"
