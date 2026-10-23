"""Reject private or machine-specific data from tracked text files."""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ALLOW_MARKER = "privacy-check: allow"
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {"LICENSE"}

_PROFILE_ROOTS = ("Users", "Documents and Settings")
WINDOWS_PROFILE = re.compile(
    r"\b[A-Za-z]:[\\/](?:"
    + "|".join(re.escape(root) for root in _PROFILE_ROOTS)
    + r")[\\/][^\\/\s]+(?:[\\/]|$)",
    re.IGNORECASE,
)
_UNIX_ROOT_PATTERN = "home" + "|" + "Users"
UNIX_HOME = re.compile(r"/(?:" + _UNIX_ROOT_PATTERN + r")/[^/\s]+(?:/|$)")
IPV4_CANDIDATE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network((address, prefix))
    for address, prefix in (
        (10 << 24, 8),
        ((172 << 24) | (16 << 16), 12),
        ((192 << 24) | (168 << 16), 16),
    )
)
SECRET_SIGNATURES = (
    (
        "private key",
        re.compile("-----BEGIN " + "(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("GitHub token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "credential-bearing URL",
        re.compile(r"https?://[^\s/:]+:[^\s/@]+@", re.IGNORECASE),
    ),
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|session[_-]?id|sid)"
    r"\s*[:=]\s*[\"']([^\"']+)[\"']",
)
SAFE_PLACEHOLDERS = {
    "changeme",
    "dummy",
    "example",
    "password",
    "redacted",
    "secret",
    "secret-session",
    "test",
}


@dataclass(frozen=True)
class Finding:
    """A privacy violation in a publishable text source."""

    path: str
    line: int
    kind: str


def _is_private_address(value: str) -> bool:
    """Return whether an IPv4 value belongs to an RFC 1918 network."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address in network for network in PRIVATE_NETWORKS)


def scan_text(path: str, text: str) -> list[Finding]:
    """Scan text and return privacy findings without exposing matched secrets."""
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        if WINDOWS_PROFILE.search(line):
            findings.append(Finding(path, line_number, "Windows user-profile path"))
        if UNIX_HOME.search(line):
            findings.append(Finding(path, line_number, "Unix user-home path"))
        address_matches = IPV4_CANDIDATE.finditer(line)
        if any(_is_private_address(match.group()) for match in address_matches):
            findings.append(Finding(path, line_number, "RFC 1918 private address"))
        for kind, pattern in SECRET_SIGNATURES:
            if pattern.search(line):
                findings.append(Finding(path, line_number, kind))
        for match in CREDENTIAL_ASSIGNMENT.finditer(line):
            value = match.group(1).strip().lower()
            is_placeholder = value in SAFE_PLACEHOLDERS or value.startswith(
                ("<", "${", "{{"),
            )
            if not is_placeholder:
                findings.append(
                    Finding(path, line_number, "literal credential assignment"),
                )
    return findings


def _tracked_text_files() -> list[Path]:
    """Return tracked files whose contents are intended to be human-readable."""
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("Git is required to enumerate publishable files")
    result = subprocess.run(  # noqa: S603 - fixed Git arguments, no user input
        [git_executable, "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    paths = result.stdout.decode().split("\0")
    return [
        REPOSITORY_ROOT / path
        for path in paths
        if path
        and (
            Path(path).suffix.lower() in TEXT_SUFFIXES
            or Path(path).name in TEXT_FILENAMES
        )
    ]


def main() -> int:
    """Scan the tracked tree and current commit message."""
    findings: list[Finding] = []
    for path in _tracked_text_files():
        relative_path = path.relative_to(REPOSITORY_ROOT).as_posix()
        findings.extend(scan_text(relative_path, path.read_text(encoding="utf-8")))

    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError("Git is required to inspect the current commit message")
    commit_message = subprocess.run(  # noqa: S603 - fixed Git arguments, no user input
        [git_executable, "log", "-1", "--format=%B", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    findings.extend(scan_text("<HEAD commit message>", commit_message))

    if findings:
        print("Privacy check failed:", file=sys.stderr)
        for finding in findings:
            print(
                f"- {finding.path}:{finding.line}: {finding.kind}",
                file=sys.stderr,
            )
        print(
            f"Use '{ALLOW_MARKER}' only for reviewed generic examples "
            "or test fixtures.",
            file=sys.stderr,
        )
        return 1

    print("Privacy check passed for tracked publishable text and the HEAD message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
