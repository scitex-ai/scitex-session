#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_io_scope.py
"""The session's scitex-io provenance scope.

scitex-io tags every load/save hook payload with ``in_scope`` and tallies lost
provenance events (a raising observer hook, a failed sidecar scan, a missing
output) per *provenance scope*. A session is exactly such a recording window,
so it opens one at start and closes it at close; the run's tally is reported
in the close metadata as ``io_provenance``. Without it, every load/save inside
a session was logged as "outside any provenance scope".
"""

from __future__ import annotations

from typing import Any, Dict, Optional

_SCOPES: Dict[str, Any] = {}


def open_io_scope(session_id: str) -> None:
    """Enter ``scitex_io.provenance_scope(session_id)`` for the run's life."""
    from scitex_io import provenance_scope

    scope = provenance_scope(session_id)
    scope.__enter__()
    _SCOPES[session_id] = scope


def close_io_scope(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Leave the run's scope; return its failure tally (None if none open)."""
    scope = _SCOPES.pop(str(session_id), None) if session_id else None
    if scope is None:
        return None
    try:
        return scope.failures()
    finally:
        scope.__exit__(None, None, None)


# EOF
