"""
Shared pytest configuration.

- Adds src/python to sys.path so test modules can `import metabolights_client`
  etc. without packaging src/python as an installed package.
- Skips tests marked @pytest.mark.live unless --run-live is passed, since
  those tests hit real external APIs.
"""

import os
import sys

import pytest

SRC_PYTHON = os.path.join(os.path.dirname(__file__), "..", "src", "python")
sys.path.insert(0, os.path.abspath(SRC_PYTHON))


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests marked 'live' (hits real external APIs)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return

    skip_live = pytest.mark.skip(reason="need --run-live option to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
