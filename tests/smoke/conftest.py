"""Smoke-test isolation: CLI subprocesses get a fake SCITEX home.

``scitex-logging`` writes a runtime log under ``$SCITEX_DIR`` — it must
land in a tmp dir, never the developer's real ``~/.scitex``.

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
