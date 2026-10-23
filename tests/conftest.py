"""Shared fixtures for Buffalo LinkStation tests."""

from __future__ import annotations

from contextlib import suppress

import pytest
from _pytest.fixtures import FixtureLookupError


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    """Enable custom integration loading in Home Assistant tests."""
    with suppress(FixtureLookupError):
        request.getfixturevalue("enable_custom_integrations")
    yield
