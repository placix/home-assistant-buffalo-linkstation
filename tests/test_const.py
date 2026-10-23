"""Tests for verified model and firmware capabilities."""

from custom_components.buffalo_linkstation.const import (
    CONF_ENABLE_REBOOT,
    CONF_ENABLE_SHUTDOWN,
    DEFAULT_TIMEOUT,
    is_reboot_enabled,
    is_shutdown_enabled,
    is_verified_shutdown,
    normalize_firmware,
    normalize_model,
)


def test_default_timeout_matches_shared_firmware_minimum():
    assert DEFAULT_TIMEOUT == 20


def test_shutdown_is_verified_for_ls220d_firmware_186():
    assert is_verified_shutdown("LS220D", "1.86")


def test_two_ls220d_identity_formats_resolve_to_same_verified_pair():
    assert is_verified_shutdown(" LS220D ", " 1.86 ")
    assert is_verified_shutdown("LS220D (SANJO)", "1.86 - 0.07")
    assert normalize_model("LS220D (SANJO)") == "LS220D"
    assert normalize_firmware("1.86 - 0.07", "LS220D (SANJO)") == "1.86"


def test_shutdown_is_verified_for_ls420d_firmware_188():
    assert is_verified_shutdown("LS420D", "1.88")


def test_shutdown_is_not_verified_for_other_combinations():
    assert not is_verified_shutdown("LS420D", "1.89")
    assert not is_verified_shutdown("LS999D", "1.88")
    assert not is_verified_shutdown("LS220D", None)
    assert not is_verified_shutdown("LS220D-EU", "1.86")
    assert not is_verified_shutdown("LS220D", "1.86-0.08")
    assert not is_verified_shutdown("LS-WVL(KOUGYOKU)", "1.75")
    assert normalize_model("LS-WVL(KOUGYOKU)") == "LS-WVL"


def test_missing_shutdown_option_uses_model_firmware_default():
    assert is_shutdown_enabled({}, "LS220D (SANJO)", "1.86 - 0.07")
    assert is_shutdown_enabled({}, "LS420D", "1.88")
    assert not is_shutdown_enabled({}, "LS420D", "1.89")
    assert not is_shutdown_enabled({}, None, None)


def test_explicit_shutdown_option_overrides_model_firmware_default():
    assert not is_shutdown_enabled({CONF_ENABLE_SHUTDOWN: False}, "LS220D", "1.86")
    assert is_shutdown_enabled({CONF_ENABLE_SHUTDOWN: True}, "unverified", "9.9")


def test_reboot_defaults_disabled_and_honors_explicit_setting():
    assert not is_reboot_enabled({})
    assert not is_reboot_enabled({CONF_ENABLE_REBOOT: False})
    assert is_reboot_enabled({CONF_ENABLE_REBOOT: True})
