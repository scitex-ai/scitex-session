"""Regression: importing tests/conftest.py must not touch site-packages or env."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


def test_conftest_leaves_site_packages_untouched():
    # Arrange
    child_code = textwrap.dedent(
        f"""\
        import os
        import runpy
        import site

        os.environ["COVERAGE_PROCESS_START"] = "SENTINEL_PROCESS_START"
        os.environ["COVERAGE_FILE"] = "SENTINEL_COVERAGE_FILE"

        def _site_dirs():
            dirs = []
            try:
                dirs.extend(site.getsitepackages())
            except Exception:
                pass
            try:
                dirs.append(site.getusersitepackages())
            except Exception:
                pass
            return dirs

        def _snapshot():
            snap = {{}}
            for d in _site_dirs():
                try:
                    snap[d] = sorted(os.listdir(d))
                except OSError:
                    snap[d] = None
            return snap

        before = _snapshot()
        runpy.run_path({str(_CONFTEST)!r}, run_name="conftest_under_test")
        after = _snapshot()
        assert before == after, "conftest.py created/modified files in site-packages"
        assert os.environ.get("COVERAGE_PROCESS_START") == "SENTINEL_PROCESS_START"
        assert os.environ.get("COVERAGE_FILE") == "SENTINEL_COVERAGE_FILE"
        """
    )
    # Act
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        capture_output=True,
        text=True,
    )
    # Assert
    assert result.returncode == 0, (
        f"child failed: {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
