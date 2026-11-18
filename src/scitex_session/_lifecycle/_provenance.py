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
                   short) instead of a script file. A ``notebook`` entry (a
                   Jupyter kernel, see :mod:`._notebook`) carries ``path`` (the
                   ``.ipynb``, or None + ``path_unresolved``), ``path_source``,
                   ``invocation`` (``cell`` | ``run`` | ``file``),
                   ``code_sha256`` (code cells only, scheme
                   ``code_hash_scheme``), ``sha256`` (whole file),
                   ``code_cells`` and the ``executed_cells`` record
``packages``       ``{distribution: version}`` for every imported top-level
                   module that belongs to an installed distribution —
                   third-party, scitex and editable installs alike; refreshed
                   at session close (:func:`collect_close_metadata`) so
                   modules imported inside the run are included
``package_provenance`` ``{distribution: {"version", "source", "fingerprint",
                   "modified_files", ...}}`` for every distribution in
                   ``packages``: PEP 610 install source (``"index"`` |
                   ``"editable"`` | ``"local_dir"`` | ``"local_archive"`` |
                   ``"remote_archive"`` | ``"vcs"``), a content fingerprint
                   of the installed wheel and the installed files edited
                   since installation (see :mod:`._dist_provenance`). A
                   version alone cannot tell a locally patched build from the
                   index release; this can. Each distribution is examined
                   once per run: at start, or at close for one imported later
                   (handed to observers inline; the on-disk run record
                   ``CONFIGS/CONFIG.yaml`` stores it once per environment and
                   keeps a ``package_provenance_ref``, see :mod:`._env_store`)
``unresolved_imports`` ``{module: location}`` imported top-level modules
                   that are neither stdlib, project-local nor attributable to
                   an installed distribution — the environment record cannot
                   reproduce them, and says so instead of omitting them
``project_root``   the script's project, resolved by
                   :func:`._project_root.resolve_project_root`: explicit
                   argument > ``$SCITEX_SESSION_PROJECT_ROOT`` > git root > nearest
                   ancestor with a project marker > working directory (when
                   it contains the script) > the script's directory (with a
                   warning). The boundary
                   ``local_modules`` uses; observers that capture file access
                   (scitex-clew) use the same boundary, so "project-local"
                   means one thing across packages
``project_root_source`` which rule chose ``project_root``: ``"argument"``,
                   ``"env"``, ``"git"``, ``"marker"``, ``"cwd"`` or
                   ``"script_dir"`` (the last resort)
``project_root_marker`` the marker that matched (``"pyproject.toml"``,
                   ``"scripts/ + config/"``, ...) when the source is
                   ``"marker"``, else ``None``
``local_modules``  ``{abs_path: sha256}`` for imported modules that live in
                   ``project_root``, i.e. code inputs that are not pinned by a
                   package version
``sdir_run``       this run's own directory (``.../RUNNING/<ID>/``)
``sdir_out``       the script's output base (where ``stx.io.save`` writes)
``started_at``     ISO-8601 start time
``notebook_path``  the notebook a Jupyter-kernel session ran in (the session's
                   script for a cell, the driver of a ``%run`` script)
``io_provenance``  (close only) scitex-io's lost-provenance tally for this run
                   (its provenance scope), or None
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

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from scitex_logging import getLogger

from ._dist_provenance import distribution_provenance
from ._entry import detect_entry
from ._environment import _imported_distributions, _local_module_hashes
from ._project_root import resolve_project_root

logger = getLogger(__name__)

#: Threads examining imported distributions (I/O bound: reads and stats).
_PROVENANCE_WORKERS = 8

_GIT_TIMEOUT_S = 30
_MAX_DIRTY_PATHS = 500


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
    project_root: Optional[Any] = None,
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
    project_root : path-like, optional
        Explicit project root; wins over ``$SCITEX_SESSION_PROJECT_ROOT`` and every
        detected root. Must be an existing directory.

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
    elif meta["entry"].get("kind") == "notebook" and meta["entry"].get("path"):
        # ``%run script.py`` from a notebook: the script is the code, the
        # notebook is what the researcher ran
        meta["notebook_path"] = str(meta["entry"]["path"])

    git = git_executable if git_executable is not None else shutil.which("git")
    git_info = _capture("git", errors, lambda: _git_state(git, script))
    if git_info is not None:
        meta["git"] = git_info

    meta.update(
        resolve_project_root(
            script,
            explicit=project_root,
            git_root=(git_info or {}).get("root"),
        )
    )
    _collect_environment(meta, errors, modules, Path(meta["project_root"]), script)

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

    Returns a JSON-serialisable mapping with ``packages``,
    ``package_provenance`` and ``local_modules`` (union of start and close;
    provenance is collected only for distributions the start record lacks),
    ``unresolved_imports``
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
    _collect_environment(
        fresh,
        errors,
        modules,
        project_root,
        script,
        known_provenance=start.get("package_provenance") or {},
    )

    meta: Dict[str, Any] = {}
    for field in ("packages", "package_provenance", "local_modules"):
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
    known_provenance: Optional[Mapping[str, Any]] = None,
) -> None:
    """Fill ``packages`` / ``package_provenance`` / ``unresolved_imports`` /
    ``local_modules``. Distributions in ``known_provenance`` (already
    examined at start, same version) are not examined again."""
    env = _capture(
        "packages", errors, lambda: _imported_distributions(modules, project_root)
    )
    if env is not None:
        dists, meta["unresolved_imports"] = env
        meta["packages"] = {name: dist.version for name, dist in dists.items()}
        known = known_provenance or {}
        todo = [
            (name, dist)
            for name, dist in dists.items()
            if (known.get(name) or {}).get("version") != dist.version
        ]
        # One stat per installed file of every imported distribution
        # (``modified_files``): ~15k stats for a scientific stack, which is
        # 0.5-1 s serially on a FUSE/squashfs image or a network filesystem.
        # The stats wait on I/O (the GIL is released), so overlap them.
        with ThreadPoolExecutor(max_workers=_PROVENANCE_WORKERS) as pool:
            infos = list(
                pool.map(lambda item: _dist_provenance_or_error(*item, errors), todo)
            )
        meta["package_provenance"] = {
            name: info for (name, _dist), info in zip(todo, infos)
        }
    local = _capture(
        "local_modules",
        errors,
        lambda: _local_module_hashes(modules, project_root, script),
    )
    if local is not None:
        meta["local_modules"] = local


def _dist_provenance_or_error(
    name: str, dist: Any, errors: Dict[str, str]
) -> Dict[str, Any]:
    """One distribution's provenance; a failure is recorded on that entry."""
    info = _capture(
        f"package_provenance[{name}]", errors, lambda: distribution_provenance(dist)
    )
    if info is None:
        return {"version": dist.version, "error": errors[f"package_provenance[{name}]"]}
    return info


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


# EOF
