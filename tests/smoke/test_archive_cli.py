"""Smoke: the archive CLI launches and answers ``--help`` (PS-211).

Subprocess-driven (``sys.executable -m ...``) so this proves the
installed entry points resolve — an in-process ``main()`` call would
not. Hermetic: no network, no credentials, no writes outside tmp dirs.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.smoke


def test_archive_cli_answers_help() -> None:
    # Arrange
    argv = [sys.executable, "-m", "scitex_session.archive", "--help"]

    # Act
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)

    # Assert
    assert (completed.returncode, "usage:" in completed.stdout) == (0, True)
