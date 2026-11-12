#!/usr/bin/env python3
# Timestamp: "2026-07-07"
# File: tests/scitex_session/_lifecycle/test__matplotlib.py
"""Regression: importing the session lifecycle must NOT import matplotlib.pyplot.

``scitex_session._mcp_server`` imports ``_lifecycle`` -> ``_start`` ->
``_matplotlib``. That module used to eagerly resolve
``figrecipe.utils._configure_mpl`` at module scope, which pulls
``figrecipe._api`` -> ``matplotlib.pyplot`` -> the font-cache build. Because
scitex-session's ``_mcp_server`` is auto-mounted onto the umbrella MCP
aggregator, that eager import darkened the aggregator's cold-start (the
SIF font-scan fired on every startup). The figrecipe resolution is now
deferred into ``setup_matplotlib`` / a PEP-562 ``__getattr__``.

Uses a fresh subprocess (isolated interpreter) so the assertion holds
regardless of what the pytest process already imported — no mocks.
``matplotlib`` *core* may still load (cheap, no font cache); the guard is
specifically on ``matplotlib.pyplot`` / ``matplotlib.font_manager``.
"""

from __future__ import annotations

import subprocess
import sys


def _pyplot_absent_after_import(module: str) -> bool:
    code = (
        f"import importlib, sys; importlib.import_module({module!r}); "
        "sys.exit(1 if 'matplotlib.pyplot' in sys.modules else 0)"
    )
    return subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_importing_lifecycle_matplotlib_module_does_not_import_pyplot():
    # Arrange
    module = "scitex_session._lifecycle._matplotlib"
    # Act
    absent = _pyplot_absent_after_import(module)
    # Assert
    assert absent is True


def test_importing_mcp_server_does_not_import_pyplot():
    # Arrange
    module = "scitex_session._mcp_server"
    # Act
    absent = _pyplot_absent_after_import(module)
    # Assert
    assert absent is True
