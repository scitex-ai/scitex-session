#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_caller.py
"""Which user code started the session: the stack walk behind "the script".

The session's script anchors its output directory, its project root and its
provenance record, so it must be the USER's file — never a frame of the
session/dev plumbing that wraps ``@session`` (decorators, MCP bridges), and,
inside IPython, never a cell's temporary file (see :mod:`._notebook`).
"""

from __future__ import annotations

import inspect
import os as _os
from typing import List, Optional, Tuple

# Top-level module-name prefixes whose frames belong to the SciTeX
# session/dev plumbing (decorators, wrappers, MCP bridges, …). When
# walking the call stack to find the *user's* script we skip frames
# whose module name starts with any of these — they are intermediate
# wrappers, not the real caller. ``scitex_session`` self-skip handles
# the @stx.session wrapper chain; ``scitex_dev`` handles agent /
# harness / decorator layers that wrap @stx.session further. We match
# on module names (via ``inspect.getmodule``) rather than filename
# substrings so that the ``tests/scitex_session/`` test tree (whose
# *files* contain ``/scitex_session/`` but whose *module* names are
# ``tests.scitex_session.…``) is not falsely flagged as internal.
_INTERNAL_MODULE_PREFIXES: Tuple[str, ...] = (
    "scitex_session.",
    "scitex_session",
    "scitex_dev.",
    "scitex_dev",
)


def _is_internal_module_name(modname: Optional[str]) -> bool:
    """Return True iff ``modname`` is a scitex_session / scitex_dev
    plumbing module (i.e. an intermediate wrapper frame, not the
    user's script)."""
    if not modname:
        return False
    return any(
        modname == prefix.rstrip(".") or modname.startswith(prefix)
        for prefix in _INTERNAL_MODULE_PREFIXES
    )


def _is_internal_frame_file(path: str) -> bool:
    """Filename-based fallback used when a frame has no resolvable
    module (e.g. dynamically-exec'd code). Matches an *installed* or
    src-tree package path — does NOT match ``tests/scitex_session/``
    paths because those are below a ``tests`` parent, not a package
    init."""
    if not path:
        return True
    norm = path.replace("\\", "/")
    # Match "/scitex_session/_..." or "/scitex_dev/_..." (underscored
    # internal submodules), or "/site-packages/scitex_session/" /
    # "/site-packages/scitex_dev/" (installed package frames). The
    # tests/scitex_session/ tree does NOT match either pattern.
    for pkg in ("scitex_session", "scitex_dev"):
        if f"/site-packages/{pkg}/" in norm:
            return True
        if f"/src/{pkg}/" in norm:
            return True
    return False


def _find_user_caller_file(hint: Optional[str] = None) -> Optional[str]:
    """Resolve the user's script path by walking the call stack outward.

    Walks ``inspect.stack()`` from innermost to outermost and returns
    the filename of the first frame whose module name does NOT start
    with ``scitex_session`` / ``scitex_dev`` (i.e. is not an
    intermediate plumbing frame). The optional ``hint`` (typically the
    ``file=`` parameter that the @stx.session wrapper threads through)
    is used when it is a non-internal path; if the hint itself points
    at an internal wrapper file (the bug operator hit on neurovista —
    the figure landed in ``site-packages/.../decorators_out/`` because
    the wrapper's ``__file__`` was used) we discard it and walk
    instead.

    Returns ``None`` when no user frame exists — the session was entered
    from ``python -c``, stdin or a REPL, whose code has no file. It never
    falls back to a package-internal frame: attributing the run to the
    session library's own module recorded that module as the run's script
    (scitex-session#65).
    """
    if hint and not _is_internal_frame_file(hint) and not _is_own_package_file(hint):
        return hint

    for frame_info in inspect.stack():
        candidate = frame_info.filename or ""
        # Skip transient stdlib / runpy frames that are also not the user.
        if candidate.startswith("<"):  # "<string>", "<stdin>", "<frozen ...>"
            continue
        mod = inspect.getmodule(frame_info.frame)
        modname = getattr(mod, "__name__", None)
        if _is_internal_module_name(modname):
            continue
        # When the frame has no resolvable module (rare; dynamically
        # exec'd code), fall back to the filename heuristic.
        if modname is None and _is_internal_frame_file(candidate):
            continue
        if _is_own_package_file(candidate):
            continue
        return candidate
    return None


def _is_own_package_file(path: str) -> bool:
    """True for a file inside the imported scitex_session package."""
    if not path:
        return False
    package_dir = _os.path.dirname(_os.path.dirname(_os.path.realpath(__file__)))
    real = _os.path.realpath(path)
    return real == package_dir or real.startswith(package_dir + _os.sep)


def _library_roots() -> Tuple[str, ...]:
    """Directories of installed code: the stdlib and every site-packages."""
    import sysconfig

    paths = sysconfig.get_paths()
    keys = ("stdlib", "platstdlib", "purelib", "platlib")
    return tuple(sorted({_os.path.realpath(paths[k]) for k in keys if paths.get(k)}))


def _is_library_file(path: str, roots: Tuple[str, ...]) -> bool:
    real = _os.path.realpath(path)
    parts = real.split(_os.sep)
    if "site-packages" in parts or "dist-packages" in parts:
        return True
    return any(real == r or real.startswith(r + _os.sep) for r in roots)


def _run_script_path() -> Optional[str]:
    """The file IPython's ``%run`` is executing (it sets ``sys.argv[0]``)."""
    import sys as _sys

    first = _sys.argv[0] if _sys.argv else ""
    if not first or not _os.path.isfile(first):
        return None
    return _os.path.realpath(_os.path.abspath(first))


def _find_user_caller(
    hint: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """``(script file or None, invocation)`` of the code that started the session.

    Outside IPython this is :func:`_find_user_caller_file` with invocation
    ``None``. Inside IPython (a Jupyter kernel or the terminal shell) the
    stack is walked inward-out past session plumbing and installed/stdlib
    frames; the first remaining frame decides:

    * an IPython cell (no file behind it) -> ``(None, "cell")``: the code is
      the notebook (or the interactive shell), never the cell's temp file;
    * the script ``%run`` is executing (``sys.argv[0]``) -> ``(script, "run")``;
    * any other file -> ``(file, "file")`` (e.g. a callback in a module).

    An explicit, non-internal ``hint`` wins (``"run"`` when it is the
    ``%run`` script).
    """
    from ._notebook import ipython_shell, is_cell_file

    if ipython_shell() is None:
        return _find_user_caller_file(hint), None
    run_script = _run_script_path()
    if hint and not _is_internal_frame_file(hint) and not _is_own_package_file(hint):
        real = _os.path.realpath(_os.path.abspath(hint))
        return hint, ("run" if real == run_script else "file")

    roots = _library_roots()
    first_file: Optional[str] = None
    for frame_info in inspect.stack():
        candidate = frame_info.filename or ""
        if is_cell_file(candidate):
            return None, "cell"
        if candidate.startswith("<"):
            continue
        mod = inspect.getmodule(frame_info.frame)
        if _is_internal_module_name(getattr(mod, "__name__", None)):
            continue
        if _is_internal_frame_file(candidate) or _is_own_package_file(candidate):
            continue
        if _is_library_file(candidate, roots):
            continue
        if run_script is not None and _os.path.realpath(candidate) == run_script:
            return candidate, "run"
        if first_file is None:
            first_file = candidate
    return first_file, ("file" if first_file else None)


def _cli_argv(invocation: Optional[str]) -> Optional[List[str]]:
    """Arguments the session's CLI parser reads (``None`` = ``sys.argv[1:]``).

    Inside IPython, ``sys.argv`` is the kernel's / shell's own command line
    (``ipykernel_launcher -f <connection file> ...``) except while ``%run``
    executes a script, when IPython sets it to the script's arguments. Only
    then is it the analysis CLI; otherwise the function's defaults apply, as
    for a script run without arguments.
    """
    import sys as _sys

    from ._notebook import ipython_shell

    if ipython_shell() is None:
        return None
    return list(_sys.argv[1:]) if invocation == "run" else []


# EOF
