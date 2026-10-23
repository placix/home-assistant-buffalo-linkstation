"""Tests for the publishable-text privacy check."""

from tools.check_privacy import scan_text


def test_detects_machine_specific_paths_and_private_addresses():
    windows_path = r"C:\Users\A\x"  # privacy-check: allow
    unix_path = "/home/a/x"  # privacy-check: allow
    private_address = "host: 192.168.1.25"  # privacy-check: allow

    assert scan_text("README.md", windows_path)[0].kind == "Windows user-profile path"
    assert scan_text("README.md", unix_path)[0].kind == "Unix user-home path"
    address_finding = scan_text("config.yaml", private_address)[0]
    assert address_finding.kind == "RFC 1918 private address"


def test_detects_obvious_secret_signatures():
    github_token = "token: ghp_12345678901234567890"  # privacy-check: allow
    private_key = "-----BEGIN PRIVATE KEY-----"  # privacy-check: allow

    assert scan_text("notes.md", github_token)[0].kind == "GitHub token"
    assert scan_text("key.pem", private_key)[0].kind == "private key"


def test_allows_documentation_addresses_and_test_placeholders():
    text = """CONF_PASSWORD: "secret"
host: 192.0.2.5
host: nas.local
cd home-assistant-buffalo-linkstation
"""

    assert scan_text("tests/example.py", text) == []


def test_allow_marker_suppresses_reviewed_fixture():
    reviewed = "host: 10.0.0.5  # privacy-check: allow"  # privacy-check: allow

    assert scan_text("tests/example.py", reviewed) == []
