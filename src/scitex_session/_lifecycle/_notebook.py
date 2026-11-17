#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_notebook.py
"""Sessions started inside a Jupyter kernel: which notebook is the code.

A ``@session`` called in a notebook cell has no script file. The frame that
called it is an IPython *cell* (``/tmp/ipykernel_<pid>/<hash>.py``: a name in
``linecache`` with no file behind it), and ``sys.argv`` is the kernel's own
command line (``ipykernel_launcher -f <connection file> ...``), not the
analysis CLI. The code that produced the outputs is the ``.ipynb`` file the
kernel is executing, so the session records THAT as its script: it anchors
the project root, the output directory (``<notebook>_out/``, the same
convention as ``<script>_out/`` and as scitex-io's notebook saves) and the
provenance record.

Which notebook? :func:`resolve_notebook` tries, first match wins (the winner
is recorded as ``entry["path_source"]``):

``env``            ``$SCITEX_NOTEBOOK_PATH`` (explicit; the SciTeX convention
                   scitex-io reads too)
``vscode``         ``__vsc_ipynb_file__`` in the user namespace (VS Code)
``jupyter``        ``__session__`` in the user namespace / ``$JPY_SESSION_NAME``
                   (set for kernels a Jupyter server starts)
``parent-cmdline`` a ``*.ipynb`` argument of the process that launched the
                   kernel (``jupyter nbconvert --execute``, ``papermill``,
                   ``jupyter execute``, nbclient scripts), ACCEPTED ONLY when
                   that notebook's code cells contain the cell being executed
                   — the parent may name a notebook it is not running
``jupyter-server`` ``scitex_context.get_notebook_path()`` (asks a running
                   Jupyter server which notebook owns this kernel)

KEEP IN SYNC: scitex-io routes a notebook's relative ``save()`` to
``<notebook>_out/`` with the same rule (``scitex_io._notebook.
resolve_notebook``). Both repositories run the byte-identical
``tests/**/conformance/notebook-path-vectors.json`` (and its runner); a change
to this rule must update BOTH implementations and those files IN BOTH
REPOSITORIES in the same change.

When nothing resolves, the run is recorded as script-less (like
``python -c``) with the reason, and a WARNING says how to name the notebook —
never as the kernel's temporary cell file.

The notebook's identity is its CODE: :func:`notebook_code_sha256` hashes the
code cells' sources only (scheme :data:`CODE_HASH_SCHEME`), so executing it
again, or saving it with new outputs / execution counts / metadata, does not
change it. scitex-clew verifies notebooks with the same definition.
"""

from __future__ import annotations

import hashlib
import json
import linecache
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from scitex_logging import getLogger

logger = getLogger(__name__)

#: Explicit notebook path (shared with scitex-io's notebook save routing).
NOTEBOOK_PATH_ENV = "SCITEX_NOTEBOOK_PATH"

#: Name of the code-identity hash, recorded next to ``code_sha256``.
CODE_HASH_SCHEME = "ipynb-code-v1"

#: At most this many executed cells are listed in ``entry["executed_cells"]``
#: (the most recent ones; ``executed_cells_total`` gives the full count).
MAX_EXECUTED_CELLS = 500


# ---------------------------------------------------------------------------
# IPython introspection
# ---------------------------------------------------------------------------


def ipython_shell() -> Any:
    """The running IPython shell, or ``None`` outside IPython."""
    ipython = sys.modules.get("IPython")
    get_ipython = getattr(ipython, "get_ipython", None)
    if get_ipython is None:
        return None
    try:
        return get_ipython()
    except Exception:  # noqa: BLE001 - an unusable IPython is "not IPython"
        return None


def is_cell_file(filename: str) -> bool:
    """True when ``filename`` names an IPython cell, not a file on disk.

    IPython compiles every cell under a synthetic name it registers in
    :mod:`linecache` (ipykernel: ``<tmp>/ipykernel_<pid>/<hash>.py``; the
    terminal shell: ``<ipython-input-N-...>``); no file exists behind it.
    """
    if not filename or ipython_shell() is None:
        return False
    if filename.startswith("<ipython-input-"):
        return True
    return filename in linecache.cache and not os.path.isfile(filename)


def current_cell_source(shell: Any = None) -> Optional[str]:
    """Raw source of the cell being executed (IPython stores it first)."""
    shell = ipython_shell() if shell is None else shell
    raw = _raw_history(shell)
    return raw[-1] if raw else None


def _raw_history(shell: Any) -> List[str]:
    manager = getattr(shell, "history_manager", None)
    raw = getattr(manager, "input_hist_raw", None)
    if not raw:
        return []
    # index 0 is a placeholder ("") so that In[n] is the n-th cell
    return [str(s) for s in list(raw)[1:]]


# ---------------------------------------------------------------------------
# Notebook code identity
# ---------------------------------------------------------------------------


def _normalise(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n")


def notebook_code_cells(path: os.PathLike) -> List[str]:
    """Sources of the notebook's code cells, in order (nbformat 4, or 3)."""
    with open(path, encoding="utf-8") as fh:
        nb = json.load(fh)
    cells = nb.get("cells")
    if cells is None:  # nbformat 3: cells live in worksheets, source in "input"
        cells = [c for ws in nb.get("worksheets") or [] for c in ws.get("cells") or []]
    sources = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        src = cell.get("source", cell.get("input", ""))
        if isinstance(src, list):
            src = "".join(src)
        sources.append(_normalise(str(src)))
    return sources


def code_cells_sha256(sources: Sequence[str]) -> str:
    """SHA-256 of code-cell sources (:data:`CODE_HASH_SCHEME`).

    The digest of the UTF-8 JSON array of the sources (newlines normalised to
    ``\\n``, compact separators, non-ASCII kept). Markdown and raw cells,
    outputs, execution counts and metadata do not enter it.
    """
    payload = json.dumps(
        [_normalise(s) for s in sources], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def notebook_code_sha256(path: os.PathLike) -> str:
    """Code identity of the notebook at ``path`` (see :func:`code_cells_sha256`)."""
    return code_cells_sha256(notebook_code_cells(path))


def _file_sha256(path: os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_cell(path: str, cell: Optional[str]) -> bool:
    if cell is None:
        return False
    want = _normalise(cell).strip()
    try:
        return any(src.strip() == want for src in notebook_code_cells(path))
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Which notebook is this kernel running?
# ---------------------------------------------------------------------------


def _existing_ipynb(value: Any, base: Optional[str] = None) -> Optional[str]:
    if not isinstance(value, str) or not value.endswith(".ipynb"):
        return None
    path = value if os.path.isabs(value) or base is None else os.path.join(base, value)
    path = os.path.realpath(os.path.abspath(path))
    return path if os.path.isfile(path) else None


def _parent_pid(environ: Dict[str, str]) -> Optional[int]:
    raw = environ.get("JPY_PARENT_PID", "")
    if raw.isdigit():
        return int(raw)
    ppid = os.getppid()
    return ppid if ppid > 1 else None


def parent_command_line(pid: int) -> Optional[tuple]:
    """``(argv, cwd)`` of process ``pid``, or ``None`` when unreadable.

    Linux ``/proc``; elsewhere psutil when it is installed.
    """
    proc = Path("/proc") / str(pid)
    if proc.is_dir():
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            cwd = os.readlink(proc / "cwd")
        except OSError:
            return None
        return [os.fsdecode(a) for a in argv if a], cwd
    try:
        import psutil  # optional
    except ImportError:
        return None
    try:
        parent = psutil.Process(pid)
        return parent.cmdline(), parent.cwd()
    except Exception:  # noqa: BLE001 - gone / access denied: nothing to read
        return None


def resolve_notebook(
    shell: Any = None, environ: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Locate the notebook this kernel is executing (see module docstring).

    Returns ``{"path": str, "source": str}``, or ``{"path": None, "reason":
    str}`` when no source names it.
    """
    environ = os.environ if environ is None else environ
    shell = ipython_shell() if shell is None else shell
    tried: List[str] = []

    explicit = environ.get(NOTEBOOK_PATH_ENV, "").strip()
    if explicit:
        path = _existing_ipynb(explicit, os.getcwd())
        if path is not None:
            return {"path": path, "source": "env"}
        logger.warning(
            "scitex-session: $%s=%r is not an existing .ipynb file; ignoring it",
            NOTEBOOK_PATH_ENV,
            explicit,
        )
        tried.append(f"${NOTEBOOK_PATH_ENV} {explicit!r} is not an existing .ipynb")

    user_ns = getattr(shell, "user_ns", None) or {}
    path = _existing_ipynb(user_ns.get("__vsc_ipynb_file__"))
    if path is not None:
        return {"path": path, "source": "vscode"}
    for value in (user_ns.get("__session__"), environ.get("JPY_SESSION_NAME")):
        # absolute (Jupyter Server 2) or relative to the server root; the
        # kernel runs in the notebook's directory, so try its name there too
        path = _existing_ipynb(value, os.getcwd())
        if path is None and isinstance(value, str):
            path = _existing_ipynb(os.path.basename(value), os.getcwd())
        if path is not None:
            return {"path": path, "source": "jupyter"}

    pid = _parent_pid(environ)
    parent = parent_command_line(pid) if pid is not None else None
    if parent is not None:
        argv, cwd = parent
        candidates = [p for p in (_existing_ipynb(a, cwd) for a in argv[1:]) if p]
        cell = current_cell_source(shell)
        for candidate in candidates:
            if _contains_cell(candidate, cell):
                return {"path": candidate, "source": "parent-cmdline"}
        if candidates:
            tried.append(
                f"the launching process names {candidates} but none of them "
                "contains the cell being executed"
            )
        else:
            tried.append("the launching process names no .ipynb file")
    else:
        tried.append("the launching process's command line is not readable")

    try:
        from scitex_context import get_notebook_path

        path = _existing_ipynb(get_notebook_path())
    except Exception as exc:  # noqa: BLE001 - recorded in the reason below
        path = None
        tried.append(f"Jupyter server query failed ({type(exc).__name__}: {exc})")
    if path is not None:
        return {"path": path, "source": "jupyter-server"}
    return {"path": None, "reason": "; ".join(tried) or "no notebook source"}


# ---------------------------------------------------------------------------
# The entry record
# ---------------------------------------------------------------------------


def notebook_entry(
    resolved: Dict[str, Any], invocation: str, shell: Any = None
) -> Dict[str, Any]:
    """Fields merged into ``entry`` (kind ``notebook``) for this session.

    ``invocation`` is ``"cell"`` (``@session`` called from a cell) or
    ``"run"`` (from a script the notebook ran with ``%run``).
    """
    shell = ipython_shell() if shell is None else shell
    fields: Dict[str, Any] = {"invocation": invocation}
    path = resolved.get("path")
    if path is None:
        fields["path"] = None
        fields["path_unresolved"] = resolved.get("reason")
        return fields
    fields["path"] = path
    fields["path_source"] = resolved.get("source")
    sources = notebook_code_cells(path)
    fields["code_hash_scheme"] = CODE_HASH_SCHEME
    fields["code_sha256"] = code_cells_sha256(sources)
    fields["sha256"] = _file_sha256(path)
    fields["code_cells"] = len(sources)
    fields.update(_executed_cells(sources, _raw_history(shell)))
    return fields


def _executed_cells(sources: List[str], history: List[str]) -> Dict[str, Any]:
    """Which cells ran, in which order, and whether the file holds them.

    Each executed cell (most recent :data:`MAX_EXECUTED_CELLS`) is listed as
    ``{"n": execution number, "sha256": of its source, "cell": index of the
    notebook code cell with that source, or None}``. Cells with identical
    sources are told apart by order: an executed cell matches the first
    such cell AFTER the previous match (else the first one).
    """
    index: Dict[str, List[int]] = {}
    for i, src in enumerate(sources):
        index.setdefault(src.strip(), []).append(i)
    executed = []
    last = -1
    for n, src in enumerate(history, start=1):
        text = _normalise(src)
        where = index.get(text.strip()) or []
        cell = next((i for i in where if i > last), where[0] if where else None)
        if cell is not None:
            last = cell
        executed.append(
            {
                "n": n,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "cell": cell,
            }
        )
    positions = [c["cell"] for c in executed]
    unsaved = sum(p is None for p in positions)
    if unsaved:
        logger.warning(
            "scitex-session: %d of the %d cells executed in this kernel are not "
            "in the notebook file as saved (unsaved or since-edited cells); the "
            "recorded notebook is not exactly the code that ran — save the "
            "notebook and re-run it top to bottom for a reproducible record",
            unsaved,
            len(executed),
        )
    known = [p for p in positions if p is not None]
    return {
        "executed_cells": executed[-MAX_EXECUTED_CELLS:],
        "executed_cells_total": len(executed),
        "executed_cells_not_in_file": unsaved,
        # top to bottom: every executed cell in the file, each after the last
        "executed_in_order": not unsaved and known == sorted(set(known)),
    }


# EOF
