"""Lifecycle exports preserve callable identity without loading archive peers."""

import subprocess
import sys

import pytest


def test_archive_import_does_not_load_plotting_or_session_start():
    # Arrange
    code = (
        "import sys; import scitex_session.archive; "
        "forbidden = {'matplotlib', 'pandas', 'scitex_io', "
        "'scitex_session._lifecycle._start', "
        "'scitex_session._lifecycle._close'}; "
        "assert not (forbidden & sys.modules.keys()), "
        "sorted(forbidden & sys.modules.keys())"
    )
    # Act
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    # Assert
    assert result.returncode == 0, result.stderr


def test_archive_export_is_the_owning_callable():
    # Arrange
    from scitex_session import _lifecycle
    from scitex_session._lifecycle._archive import archive_session_dir
    # Act
    result = _lifecycle.archive_session_dir
    # Assert
    assert result is archive_session_dir


def test_lifecycle_directory_preserves_all_existing_exports():
    # Arrange
    from scitex_session import _lifecycle
    expected = {
        "start", "close", "running2finished", "archive_session_dir",
        "restore_session_archive", "archive_existing", "restore_existing",
    }
    # Act
    result = {name for name in dir(_lifecycle) if not name.startswith("_")}
    # Assert
    assert result == expected


def test_unknown_lifecycle_export_raises_attribute_error():
    # Arrange
    from scitex_session import _lifecycle
    # Act
    def lookup_unknown():
        return getattr(_lifecycle, "unknown_session_review_export")
    # Assert
    with pytest.raises(AttributeError, match="no attribute"):
        lookup_unknown()
