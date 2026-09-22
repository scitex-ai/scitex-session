"""E2E: a session dir survives an archive → restore round-trip (PS-212).

Drives the real ``archive_session_dir`` / ``restore_session_archive``
subsystem against real files in tmp dirs — no network, no mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_session._lifecycle._archive import (
    archive_session_dir,
    restore_session_archive,
)

pytestmark = pytest.mark.e2e


def _make_session_dir(root: Path) -> Path:
    session_dir = root / "2026Y-09M-22D-00h00m00s_ABCD"
    session_dir.mkdir(parents=True)
    (session_dir / "note.txt").write_text("hello-session")
    return session_dir


def test_archive_creates_tarball(tmp_path: Path) -> None:
    # Arrange
    session_dir = _make_session_dir(tmp_path)

    # Act
    archive_path = archive_session_dir(session_dir, remove_src=False)

    # Assert
    assert archive_path.is_file()


def test_restore_recovers_session_files(tmp_path: Path) -> None:
    # Arrange
    session_dir = _make_session_dir(tmp_path)
    archive_path = archive_session_dir(session_dir, remove_src=False)

    # Act
    restored = restore_session_archive(archive_path, dest_dir=tmp_path / "back")

    # Assert
    assert (restored / "note.txt").read_text() == "hello-session"
