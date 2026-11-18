#!/usr/bin/env python3
"""Tests for examples/measure_run_record_size.py."""

import os
import py_compile
import subprocess
import sys
from pathlib import Path

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "examples" / "measure_run_record_size.py"
)


def test_measure_example_compiles_without_error():
    # Arrange
    path = str(EXAMPLE)
    # Act
    compiled = py_compile.compile(path, doraise=True)
    # Assert
    assert compiled


def test_measure_example_refuses_to_run_without_a_store_dsn(tmp_path):
    # Arrange: an explicit environment without SCITEX_STORE_DSN
    env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    # Act
    res = subprocess.run(
        [sys.executable, str(EXAMPLE), "--out-dir", str(tmp_path), "--sessions", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Assert
    assert (res.returncode, "SCITEX_STORE_DSN" in res.stderr) == (2, True)


# EOF
