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
``packages``       ``{distribution: version}`` for every imported top-level
                   module that belongs to an installed distribution
``local_modules``  ``{abs_path: sha256}`` for imported modules that live in
                   the script's project (git root, else script dir), i.e. code
                   inputs that are not pinned by a package version
``sdir_run``       this run's own directory (``.../RUNNING/<ID>/``)
``sdir_out``       the script's output base (where ``stx.io.save`` writes)
``started_at``     ISO-8601 start time
``notebook_path``  only for ``.ipynb`` sessions
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
from typing import Any, Callable, Dict, Mapping, Optional

from scitex_logging import getLogger

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
    modules: Optional[Mapping[str, Any]] = None,
    git_executable: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect the context needed to re-run this session.

    Parameters
    ----------
    script_path : str or None
        The user's script (or notebook) path.
    args : argparse.Namespace or mapping, optional
        Parsed CLI arguments.
    seed : int, optional
        The RNG seed the session initialised.
    sdir_run, sdir_out : str, optional
        This run's directory and the script's output base.
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
    }
    if script is not None and script.suffix == ".ipynb":
        meta["notebook_path"] = str(script)

    git = git_executable if git_executable is not None else shutil.which("git")
    git_info = _capture("git", errors, lambda: _git_state(git, script))
    if git_info is not None:
        meta["git"] = git_info

    project_root = _project_root(git_info, script)
    collectors: Dict[str, Callable[[], Any]] = {
        "packages": lambda: _package_versions(modules),
        "local_modules": lambda: _local_module_hashes(modules, project_root, script),
    }
    for field, collect in collectors.items():
        value = _capture(field, errors, collect)
        if value is not None:
            meta[field] = value

    meta["capture_errors"] = errors
    return json.loads(json.dumps(meta, default=str))


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
    return script.parent if script is not None else None


def _module_file(module: Any) -> Optional[Path]:
    path = getattr(module, "__file__", None)
    if not path or not str(path).endswith(".py"):
        return None
    p = Path(path)
    return p.resolve() if p.is_file() else None


def _package_versions(modules: Mapping[str, Any]) -> Dict[str, str]:
    from importlib.metadata import packages_distributions, version

    top_level = {name.split(".", 1)[0] for name in list(modules)}
    dist_map = packages_distributions()
    versions: Dict[str, str] = {}
    for name in sorted(top_level):
        for dist in dist_map.get(name, ()):
            if dist not in versions:
                versions[dist] = version(dist)
    return versions


def _local_module_hashes(
    modules: Mapping[str, Any],
    project_root: Optional[Path],
    script: Optional[Path],
) -> Dict[str, str]:
    if project_root is None:
        return {}
    hashes: Dict[str, str] = {}
    for module in list(modules.values()):
        path = _module_file(module)
        if path is None or path == script:
            continue
        if any(marker in path.parts for marker in _INSTALLED_MARKERS):
            continue
        try:
            path.relative_to(project_root)
        except ValueError:
            continue
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


# EOF
