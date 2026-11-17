#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_provenance.py
"""Re-run metadata handed to provenance observers at session start.

A provenance record of *input files -> script -> output files* is not enough
to re-run a session: the same script with the same inputs produces different
outputs under different CLI arguments, RNG seed, working directory, commit,
package versions or local helper modules. :func:`collect_run_metadata` gathers
exactly that context and the session passes it, as the ``metadata`` argument,
to every registered session-start hook (clew stores it on the run record).

Field names (stable; observers may read them):

``argv``           ``sys.argv`` verbatim
``args``           parsed CLI arguments (``{name: value}``)
``seed``           RNG seed given to ``RandomStateManager``
``cwd``            working directory (relative paths resolve against it)
``python``         ``{"version", "executable"}``
``git``            ``{"repository": False}`` or ``{"root", "commit", "branch",
                   "dirty", "dirty_paths"}`` for the repo holding the script
``entry``          how the process was entered (see :mod:`._entry`):
                   ``{"kind": "script"|"module"|"command"|"stdin"|
                   "interactive"|"notebook"|"unknown", ...}``; a ``python -c``
                   entry carries ``command_sha256`` (and ``command`` when
                   short) instead of a script file
``packages``       ``{distribution: version}`` for every imported top-level
                   module that belongs to an installed distribution —
                   third-party, scitex and editable installs alike; refreshed
                   at session close (:func:`collect_close_metadata`) so
                   modules imported inside the run are included
``unresolved_imports`` ``{module: location}`` imported top-level modules
                   that are neither stdlib, project-local nor attributable to
                   an installed distribution — the environment record cannot
                   reproduce them, and says so instead of omitting them
``project_root``   the script's project: the git root, else the script's
                   directory, else (no script: ``-c`` / stdin / REPL) the
                   working directory. The boundary
                   ``local_modules`` uses; observers that capture file access
                   (scitex-clew) use the same boundary, so "project-local"
                   means one thing across packages
``local_modules``  ``{abs_path: sha256}`` for imported modules that live in
                   ``project_root``, i.e. code inputs that are not pinned by a
                   package version
``sdir_run``       this run's own directory (``.../RUNNING/<ID>/``)
``sdir_out``       the script's output base (where ``stx.io.save`` writes)
``started_at``     ISO-8601 start time
``notebook_path``  only for ``.ipynb`` sessions
``sdir_run_final`` (close only) the run directory after the session moved it
                   from ``RUNNING/`` to ``FINISHED_*/``
``relocations``    (close only) ``[{"from", "to", ...}]``: every path an
                   observer recorded under ``from`` now lives under ``to``
                   (``archive`` is set when ``to`` is then collapsed into
                   that archive file)
``close_capture_errors`` (close only) like ``capture_errors``, for the
                   close-time collection
``capture_errors`` ``{field: "Type: message"}`` for every field that could
                   not be collected — never silently omitted

Every value is JSON-serialisable (non-JSON values are ``str()``-ed), because
observers persist the mapping as JSON and a serialisation error there would
lose the whole run record.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import unquote, urlparse

from scitex_logging import getLogger

from ._entry import detect_entry

logger = getLogger(__name__)

_GIT_TIMEOUT_S = 30
_MAX_DIRTY_PATHS = 500
_INSTALLED_MARKERS = ("site-packages", "dist-packages")


def collect_run_metadata(
    script_path: Optional[str],
    args: Any = None,
    seed: Optional[int] = None,
    sdir_run: Optional[str] = None,
    sdir_out: Optional[str] = None,
    *,
    entry: Optional[Mapping[str, Any]] = None,
    modules: Optional[Mapping[str, Any]] = None,
    git_executable: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect the context needed to re-run this session.

    Parameters
    ----------
    script_path : str or None
        The user's script (or notebook) path; ``None`` when the session was
        not started from a file (``python -c``, stdin, REPL).
    args : argparse.Namespace or mapping, optional
        Parsed CLI arguments.
    seed : int, optional
        The RNG seed the session initialised.
    sdir_run, sdir_out : str, optional
        This run's directory and the script's output base.
    entry : mapping, optional
        How the process was entered (:func:`._entry.detect_entry`); detected
        here when not given.
    modules : mapping, optional
        Module table to inspect for packages / local modules. Defaults to
        ``sys.modules``.
    git_executable : str, optional
        ``git`` binary. Defaults to ``shutil.which("git")``.

    Returns
    -------
    dict
        JSON-serialisable metadata; see the module docstring for fields.
        A field that could not be collected is absent and its error is in
        ``capture_errors`` (and logged as a warning).
    """
    modules = sys.modules if modules is None else modules
    script = Path(script_path).resolve() if script_path else None
    errors: Dict[str, str] = {}

    meta: Dict[str, Any] = {
        "argv": list(sys.argv),
        "args": _args_to_mapping(args),
        "seed": seed,
        "cwd": os.getcwd(),
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
        "sdir_run": str(sdir_run) if sdir_run else None,
        "sdir_out": str(sdir_out) if sdir_out else None,
        "started_at": datetime.now().isoformat(),
        "entry": dict(entry) if entry is not None else detect_entry(),
    }
    if script is not None and script.suffix == ".ipynb":
        meta["notebook_path"] = str(script)

    git = git_executable if git_executable is not None else shutil.which("git")
    git_info = _capture("git", errors, lambda: _git_state(git, script))
    if git_info is not None:
        meta["git"] = git_info

    project_root = _project_root(git_info, script)
    meta["project_root"] = str(project_root) if project_root is not None else None
    _collect_environment(meta, errors, modules, project_root, script)

    meta["capture_errors"] = errors
    return json.loads(json.dumps(meta, default=str))


def collect_close_metadata(
    start_metadata: Optional[Mapping[str, Any]],
    *,
    script_path: Optional[str] = None,
    relocations: Optional[List[Dict[str, Any]]] = None,
    sdir_run_final: Optional[str] = None,
    modules: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Close-time additions to the run metadata (handed to close hooks).

    The start-time snapshot of ``sys.modules`` misses everything the run
    imports *inside* the decorated function (a lazily imported analysis
    library is the common case), so the environment is re-collected here
    and merged with the start snapshot. ``relocations`` /
    ``sdir_run_final`` describe where the session moved its run directory
    (see :func:`~._close.running2finished`).

    Returns a JSON-serialisable mapping with ``packages`` and
    ``local_modules`` (union of start and close), ``unresolved_imports``
    (re-evaluated for every module still imported), ``relocations``,
    ``sdir_run_final`` and ``close_capture_errors``.
    """
    modules = sys.modules if modules is None else modules
    start = dict(start_metadata or {})
    root = start.get("project_root")
    project_root = Path(root) if root else None
    script = Path(script_path).resolve() if script_path else None
    errors: Dict[str, str] = {}
    fresh: Dict[str, Any] = {}
    _collect_environment(fresh, errors, modules, project_root, script)

    meta: Dict[str, Any] = {}
    for field in ("packages", "local_modules"):
        merged = dict(start.get(field) or {})
        merged.update(fresh.get(field) or {})
        meta[field] = dict(sorted(merged.items()))
    # A start-time verdict on a module that is still imported is superseded
    # by the close-time one; keep only verdicts on modules since unloaded.
    unresolved = {
        name: where
        for name, where in (start.get("unresolved_imports") or {}).items()
        if name not in modules
    }
    unresolved.update(fresh.get("unresolved_imports") or {})
    meta["unresolved_imports"] = dict(sorted(unresolved.items()))
    meta["relocations"] = list(relocations or [])
    meta["sdir_run_final"] = sdir_run_final
    # Its own key: observers merge this mapping into the start metadata, and
    # a ``capture_errors`` key here would overwrite the start-time errors.
    meta["close_capture_errors"] = errors
    return json.loads(json.dumps(meta, default=str))


def _collect_environment(
    meta: Dict[str, Any],
    errors: Dict[str, str],
    modules: Mapping[str, Any],
    project_root: Optional[Path],
    script: Optional[Path],
) -> None:
    """Fill ``packages`` / ``unresolved_imports`` / ``local_modules``."""
    env = _capture(
        "packages", errors, lambda: _imported_distributions(modules, project_root)
    )
    if env is not None:
        meta["packages"], meta["unresolved_imports"] = env
    local = _capture(
        "local_modules",
        errors,
        lambda: _local_module_hashes(modules, project_root, script),
    )
    if local is not None:
        meta["local_modules"] = local


def _capture(field: str, errors: Dict[str, str], collect: Callable[[], Any]) -> Any:
    """Run one collector; record (and warn about) its failure instead of hiding it."""
    try:
        return collect()
    except Exception as exc:  # noqa: BLE001 - recorded + warned, not swallowed
        errors[field] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "scitex-session: could not capture run metadata %r (%s); the "
            "provenance record will lack it — see metadata['capture_errors']",
            field,
            errors[field],
        )
        return None


def _args_to_mapping(args: Any) -> Optional[Dict[str, Any]]:
    if args is None:
        return None
    if hasattr(args, "__dict__") and not isinstance(args, Mapping):
        return dict(vars(args))
    return dict(args)


def _git_state(git: Optional[str], script: Optional[Path]) -> Dict[str, Any]:
    if git is None:
        raise FileNotFoundError("git executable not found on PATH")
    where = (script.parent if script is not None else Path.cwd()).resolve()

    def run(*cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [git, "-C", str(where), *cmd],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )

    inside = run("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {"repository": False}

    def out(*cmd: str) -> str:
        res = run(*cmd)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(cmd)} failed: {res.stderr.strip()}")
        return res.stdout

    root = out("rev-parse", "--show-toplevel").strip()
    commit_res = run("rev-parse", "HEAD")
    # A fresh repository with no commit yet has no HEAD; that is a state,
    # not a failure.
    commit = commit_res.stdout.strip() if commit_res.returncode == 0 else None
    branch = out("rev-parse", "--abbrev-ref", "HEAD").strip() if commit else None
    porcelain = out("status", "--porcelain", "--untracked-files=normal")
    dirty_paths = [line[3:] for line in porcelain.splitlines() if line.strip()]
    info: Dict[str, Any] = {
        "root": root,
        "commit": commit,
        "branch": branch,
        "dirty": bool(dirty_paths),
        "dirty_paths": dirty_paths[:_MAX_DIRTY_PATHS],
    }
    if len(dirty_paths) > _MAX_DIRTY_PATHS:
        info["dirty_paths_truncated"] = len(dirty_paths)
    return info


def _project_root(git_info: Optional[Dict[str, Any]], script: Optional[Path]):
    if git_info and git_info.get("root"):
        return Path(git_info["root"]).resolve()
    if script is not None:
        return script.parent
    # No script file (``python -c`` / stdin / REPL): relative paths, and so
    # the files the run touches, resolve against the working directory.
    return Path.cwd().resolve()


def _module_file(module: Any) -> Optional[Path]:
    path = getattr(module, "__file__", None)
    if not path or not str(path).endswith(".py"):
        return None
    p = Path(path)
    return p.resolve() if p.is_file() else None


def _module_location(module: Any) -> Optional[Path]:
    """Where a module was loaded from: its file (any suffix) or package dir."""
    path = getattr(module, "__file__", None)
    if path:
        return Path(path).resolve()
    for entry in list(getattr(module, "__path__", None) or ()):
        return Path(entry).resolve()  # namespace package: its first portion
    return None


def _is_installed(path: Path) -> bool:
    return any(marker in path.parts for marker in _INSTALLED_MARKERS)


def _interpreter_roots() -> List[Path]:
    """The interpreter's own trees (stdlib, venv prefix): never project code."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    return [Path(r).resolve() for r in sorted(roots) if r]


def _under(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _is_stdlib(name: str, module: Any, location: Optional[Path]) -> bool:
    if name in _STDLIB_NAMES:
        return True
    spec = getattr(module, "__spec__", None)
    if getattr(spec, "origin", None) in ("built-in", "frozen"):
        return True
    # Python < 3.10 has no ``sys.stdlib_module_names``: a module inside the
    # base interpreter tree but outside any site-packages is stdlib.
    return (
        location is not None
        and not _is_installed(location)
        and _under(location, [Path(sys.base_prefix).resolve()])
    )


_STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ())) | frozenset(
    sys.builtin_module_names
)
_NOT_A_MODULE = ("__main__", "__mp_main__")
_EXTENSION_SUFFIXES = (".so", ".pyd")


def _record_top_level_names(record: str) -> List[str]:
    """Importable top-level names a distribution's RECORD installs."""
    names = set()
    for line in record.splitlines():
        path = line.rsplit(",", 2)[0].strip().strip('"')
        if not path or path.startswith(".."):
            continue
        head, sep, _ = path.partition("/")
        if sep:  # a directory: package dir, unless *.dist-info / *.data
            if "." not in head and head != "__pycache__":
                names.add(head)
        elif head.endswith(".py"):
            names.add(head[:-3])
        elif head.endswith(_EXTENSION_SUFFIXES):
            names.add(head.split(".", 1)[0])
    return sorted(names)


def _editable_root(dist: Any) -> Optional[Path]:
    """Source directory of an editable (``pip install -e``) distribution.

    Editable installs often ship no ``top_level.txt`` and a RECORD that lists
    only a ``.pth`` / finder hook, so their modules cannot be named from the
    metadata; PEP 610 ``direct_url.json`` names the source tree instead.
    """
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    info = json.loads(raw)
    if not (info.get("dir_info") or {}).get("editable"):
        return None
    url = urlparse(info.get("url", ""))
    if url.scheme != "file":
        return None
    return Path(unquote(url.path)).resolve()


def _distribution_index() -> Tuple[Dict[str, List[Any]], List[Tuple[Path, Any]]]:
    """``{top-level module: [distribution]}`` plus editable source roots.

    Reads only each distribution's ``top_level.txt`` (``RECORD`` when absent)
    and ``direct_url.json`` as text; METADATA (name, version) is parsed only
    for the distributions that turn out to own an imported module. Tens of
    milliseconds for an environment of hundreds of distributions, where
    ``importlib.metadata.packages_distributions()`` takes seconds. Order is
    ``sys.path`` order, so a shadowed duplicate comes after the one Python
    imports.
    """
    from importlib.metadata import distributions

    by_module: Dict[str, List[Any]] = {}
    editable: List[Tuple[Path, Any]] = []
    for dist in distributions():
        top = dist.read_text("top_level.txt")
        names = (
            top.split()
            if top
            else _record_top_level_names(dist.read_text("RECORD") or "")
        )
        for module_name in names:
            by_module.setdefault(module_name, []).append(dist)
        root = _editable_root(dist)
        if root is not None:
            editable.append((root, dist))
    return by_module, editable


def _imported_distributions(
    modules: Mapping[str, Any], project_root: Optional[Path]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Distributions behind every imported top-level module.

    Returns ``(packages, unresolved)``: ``{dist: version}`` and
    ``{module: location}`` for imported modules that are not stdlib, not
    project code and not attributable to any installed distribution.
    """
    by_module, editable = _distribution_index()
    interpreter = _interpreter_roots()
    search_roots = [
        Path(entry).resolve() for entry in sys.path if entry and _is_installed(Path(entry))
    ]
    packages: Dict[str, str] = {}
    unresolved: Dict[str, str] = {}

    def add(owners: Iterable[Any]) -> None:
        for dist in owners:
            name = dist.metadata["Name"]
            if name:  # first on sys.path wins, as on import
                packages.setdefault(name, dist.version)

    for name in sorted({key.split(".", 1)[0] for key in list(modules)}):
        if name in _NOT_A_MODULE:
            continue
        module = modules.get(name)
        location = _module_location(module) if module is not None else None
        if location is None:
            # No file and no package path: a module object created at run
            # time (Cython/pybind11 runtime modules, ``types.ModuleType``),
            # not code loaded from anywhere — nothing to install. A
            # distribution-backed name still resolves.
            add(by_module.get(name) or ())
            continue
        if _is_stdlib(name, module, location):
            continue
        if (
            project_root is not None
            and not _is_installed(location)
            and not _under(location, interpreter)
            and _under(location, [project_root])
        ):
            continue  # project code: hashed in ``local_modules`` instead
        candidates = (by_module.get(name) or []) + (
            # an extension registered under a top-level name but shipped
            # inside another package (``pandas/_libs/_cyutility*.so``)
            by_module.get(_installed_top_level(location, search_roots) or "") or []
        )
        # Only a distribution whose files are where the module was loaded
        # from backs it: a checkout on PYTHONPATH shadowing an installed copy
        # of the same name runs the checkout, not the installed version.
        owners = [d for d in candidates if _under(location, [_dist_base(d)])] or [
            dist for root, dist in editable if _under(location, [root])
        ]
        if owners:
            add(owners)
            continue
        unresolved[name] = str(location)
    return dict(sorted(packages.items())), unresolved


def _dist_base(dist: Any) -> Path:
    """Directory a (non-editable) distribution installed its files into."""
    return Path(dist.locate_file("")).resolve()


def _installed_top_level(location: Path, search_roots: List[Path]) -> Optional[str]:
    """Top-level name of the installed tree ``location`` lives in, if any."""
    for root in search_roots:
        try:
            rel = location.relative_to(root)
        except ValueError:
            continue
        head = rel.parts[0] if len(rel.parts) > 1 else None
        return head
    return None


def _local_module_hashes(
    modules: Mapping[str, Any],
    project_root: Optional[Path],
    script: Optional[Path],
) -> Dict[str, str]:
    if project_root is None:
        return {}
    interpreter = _interpreter_roots()
    hashes: Dict[str, str] = {}
    for module in list(modules.values()):
        path = _module_file(module)
        if path is None or path == script:
            continue
        if _is_installed(path) or _under(path, interpreter):
            continue
        try:
            path.relative_to(project_root)
        except ValueError:
            continue
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


# EOF
