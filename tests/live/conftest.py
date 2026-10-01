"""Live tests spend money: skipped unless R2R_LIVE=1 (and the owner approved the cost)."""

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("R2R_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set R2R_LIVE=1 after owner approves the cost")
    for item in items:
        if "tests/live" in str(item.path):
            item.add_marker(skip)
