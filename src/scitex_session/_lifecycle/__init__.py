#!/usr/bin/env python3
# Timestamp: "2026-02-01 (ywatanabe)"
# File: /home/ywatanabe/proj/scitex-python/src/scitex/session/_lifecycle/__init__.py
"""Session lifecycle management - refactored subpackage.

This package contains the split modules from the original _lifecycle.py:
- _start.py: Session start function
- _close.py: Session close and running2finished functions
- _config.py: Configuration setup
- _matplotlib.py: Matplotlib configuration
- _utils.py: Utility functions

Archive exports do not initialize plotting or the session lifecycle.
"""

from importlib import import_module as _import_module

_LAZY_EXPORTS = {
    "start": ("._start", "start"),
    "close": ("._close", "close"),
    "running2finished": ("._close", "running2finished"),
    "archive_session_dir": ("._archive", "archive_session_dir"),
    "restore_session_archive": ("._archive", "restore_session_archive"),
    "archive_existing": ("._archive", "archive_existing"),
    "restore_existing": ("._archive", "restore_existing"),
}


def __getattr__(name):
    """Resolve lifecycle functions when requested, retaining callable identity."""
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(_import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    "start",
    "close",
    "running2finished",
    "archive_session_dir",
    "restore_session_archive",
    "archive_existing",
    "restore_existing",
]

# EOF
