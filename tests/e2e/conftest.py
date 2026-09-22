"""E2E fixtures: fake home only — no blanket skip gate.

Per the layered-testing leaf, e2e runs on every PR. The only legitimate
skips are per-test, keyed to a missing subsystem (missing binary, missing
import, missing GPU). These tests need none: they drive the real
archive/restore subsystem against real files in tmp dirs.

No monkeypatch (PA-306): explicit save/restore with try/finally.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_scitex_home(tmp_path: Path) -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in ("SCITEX_DIR",)}
    os.environ["SCITEX_DIR"] = str(tmp_path / "scitex-home")
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
