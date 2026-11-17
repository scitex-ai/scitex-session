#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_entry.py
"""How the Python process was entered — the provenance of "the script".

A session's provenance names the code that produced its outputs. For
``python script.py`` that is a file; for ``python -c "..."``, a here-doc on
stdin or an interactive interpreter there is no file at all, and attributing
the run to whatever file the call stack happens to show (in practice the
session library's own lifecycle module) records a spurious executable.

:func:`detect_entry` classifies the entry and, for ``-c``, records the
command text itself (its SHA-256 always; the text when short), so the run can
be identified and replayed without pretending it came from a file.

``kind`` values (stable; observers may read them):

``script``       ``python path/to/file.py`` (``path`` is the file)
``module``       ``python -m pkg.mod`` (``module`` + ``path``)
``command``      ``python -c TEXT`` (``command_sha256``, ``command_length``,
                 ``command`` when at most :data:`MAX_COMMAND_CHARS`)
``stdin``        program read from stdin (``python - < file``, here-doc);
                 its text is not recoverable
``interactive``  REPL / terminal IPython
``notebook``     Jupyter kernel
``unknown``      embedded interpreter or anything else
"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import Any, Dict, Optional, Sequence

#: Commands up to this many characters are stored verbatim; longer ones are
#: identified by hash and length only.
MAX_COMMAND_CHARS = 4096


def detect_entry(
    *,
    main_module: Any = None,
    argv: Optional[Sequence[str]] = None,
    orig_argv: Optional[Sequence[str]] = None,
    interactive: Optional[bool] = None,
) -> Dict[str, Any]:
    """Classify how this interpreter was started (see module docstring).

    Every parameter defaults to the live interpreter state; they exist so a
    caller holding a different state (a test's subprocess) can pass it.
    """
    main = sys.modules.get("__main__") if main_module is None else main_module
    argv = list(sys.argv if argv is None else argv)
    if orig_argv is None:
        orig_argv = getattr(sys, "orig_argv", None)  # Python >= 3.10
    first = argv[0] if argv else ""

    kernel = _ipython_kind()
    if kernel is not None:
        return {"kind": kernel}
    if first == "-c":
        return _command_entry(argv, orig_argv)
    spec = getattr(main, "__spec__", None)
    main_file = getattr(main, "__file__", None)
    if spec is not None and getattr(spec, "name", None) and main_file:
        return {
            "kind": "module",
            "module": spec.name.removesuffix(".__main__"),
            "path": os.path.realpath(main_file),
        }
    if main_file and os.path.isfile(main_file):
        return {"kind": "script", "path": os.path.realpath(main_file)}
    if first in ("", "-"):
        if interactive is None:
            interactive = bool(
                hasattr(sys, "ps1") or sys.flags.interactive or _stdin_is_tty()
            )
        return {"kind": "interactive" if interactive and first == "" else "stdin"}
    return {"kind": "unknown"}


def has_script_file(entry: Dict[str, Any]) -> bool:
    """True when the entry is a real file (script / ``-m`` module)."""
    return entry.get("kind") in ("script", "module")


def _command_entry(
    argv: Sequence[str], orig_argv: Optional[Sequence[str]]
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"kind": "command"}
    text = _command_text(argv, orig_argv)
    if text is None:
        entry["command_sha256"] = None
        entry["command_unavailable"] = "sys.orig_argv not available (Python < 3.10)"
        return entry
    entry["command_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    entry["command_length"] = len(text)
    if len(text) <= MAX_COMMAND_CHARS:
        entry["command"] = text
    return entry


def _command_text(
    argv: Sequence[str], orig_argv: Optional[Sequence[str]]
) -> Optional[str]:
    """The ``-c`` program text.

    ``sys.argv`` is ``["-c", *args]``; ``sys.orig_argv`` is
    ``[python, *interpreter_options, TEXT, *args]`` (options may be combined,
    e.g. ``-Bc``), so the text sits right before the trailing ``args``.
    """
    if not orig_argv:
        return None
    index = len(orig_argv) - len(argv)
    if index < 1:
        return None
    return orig_argv[index]


def _ipython_kind() -> Optional[str]:
    ipython = sys.modules.get("IPython")
    get_ipython = getattr(ipython, "get_ipython", None)
    if get_ipython is None:
        return None
    try:
        shell = get_ipython()
    except Exception:  # noqa: BLE001 - an unusable IPython is "not IPython"
        return None
    if shell is None:
        return None
    return "notebook" if "ipykernel" in sys.modules else "interactive"


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):  # closed / detached stdin
        return False


# EOF
