#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_project_root.py
"""Which directory is "the project" a session belongs to.

``project_root`` is the boundary every provenance observer uses for
"project-local": scitex-session hashes imported modules under it
(``local_modules``) and scitex-clew records files under it as project
inputs/outputs, everything else as *external*. A root that is too small
(the script's own ``scripts/`` folder) turns the project's own ``data/``
and ``config/`` into external inputs; a root that is too large (``$HOME``)
makes everything project-local. Researchers often do not use git, so the
VCS root cannot be the only signal.

Detection order (first match wins; the winning rule is recorded as
``project_root_source``):

1. ``argument`` — an explicit root: ``@session(project_root=...)`` /
   ``start(project_root=...)``.
2. ``env`` — ``$SCITEX_SESSION_PROJECT_ROOT``.
   An explicit root must be an existing directory (else ``ValueError``).
3. ``git`` — the git work-tree root holding the script (unchanged from
   scitex-session <= 0.3.6), unless it is at or above the search ceiling
   (a dotfiles repository at ``$HOME`` is not a research project).
4. ``marker`` — the nearest ancestor of the script's directory (itself
   included) holding a project marker, see :data:`PROJECT_MARKERS`
   (``project_root_marker`` names which one).
5. ``cwd`` — the working directory, when it is a proper ancestor of the
   script's directory (the researcher ran ``python scripts/run.py`` from
   the project directory).
6. ``script_dir`` — the script's own directory, with a WARNING: nothing
   identified a larger project, so files outside it count as external.

Without a script file (``python -c`` / stdin / REPL) rules 4-6 start from
the working directory instead, and the last resort is the working
directory itself (``project_root_source == "cwd"``, no warning: relative
paths resolve against it).

The upward search never reaches ``/`` and never reaches ``$HOME`` or
above when the start lies under ``$HOME`` (``~/.scitex/`` and stray
``~/pyproject.toml`` files would otherwise make the whole home directory
"the project"), and it does not cross a filesystem boundary (mount point).

``.scitex/`` is deliberately NOT a marker: SciTeX tools create it as
per-tool runtime state wherever they run (``~/.scitex/``; scitex-clew's
``<cwd>/.scitex/clew/project-id``), so its presence says where something
once ran, not where a project starts — a run from ``scripts/`` would plant
one there and pin every later run's root to ``scripts/``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from scitex_logging import getLogger

logger = getLogger(__name__)

#: Environment variable naming the project root explicitly.
PROJECT_ROOT_ENV = "SCITEX_SESSION_PROJECT_ROOT"

#: Entries whose presence marks a directory as a project root, in the order
#: reported when several are present in the same directory. A tuple entry
#: means "all of these exist" (the SciTeX research layout, see
#: ``scitex_template._project._directory_structure.PROJECT_STRUCTURE``).
PROJECT_MARKERS: Tuple[Any, ...] = (
    ".git",
    ".hg",
    ".svn",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "scitex/.scitex-structure-version",  # scitex-template minimal layout
    ("scripts", "config"),  # SciTeX research layout: config/ beside scripts/
)


def resolve_project_root(
    script: Optional[Path],
    *,
    explicit: Optional[os.PathLike] = None,
    git_root: Optional[os.PathLike] = None,
    cwd: Optional[Path] = None,
    environ: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve the project root for ``script`` (see module docstring).

    Parameters
    ----------
    script : Path or None
        The resolved entry script / notebook; ``None`` without one.
    explicit : path-like, optional
        A root passed by the caller (``@session(project_root=...)``).
    git_root : path-like, optional
        The git work-tree root holding the script, when there is one.
    cwd : Path, optional
        Working directory; defaults to ``Path.cwd()``.
    environ : mapping, optional
        Environment; defaults to ``os.environ``.

    Returns
    -------
    dict
        ``{"project_root": str, "project_root_source": str,
        "project_root_marker": str or None}``.

    Raises
    ------
    ValueError
        If an explicit root (argument or ``$SCITEX_SESSION_PROJECT_ROOT``) is not an
        existing directory.
    """
    environ = os.environ if environ is None else environ
    cwd = (cwd or Path.cwd()).resolve()

    start = script.parent if script is not None else cwd

    if explicit is not None and str(explicit) != "":
        return _explicit(explicit, "argument", "project_root argument", start)
    from_env = environ.get(PROJECT_ROOT_ENV, "").strip()
    if from_env:
        return _explicit(from_env, "env", f"${PROJECT_ROOT_ENV}", start)

    ceiling = _ceiling(start, environ)

    if git_root:
        git = Path(git_root).resolve()
        if _strictly_below(git, ceiling):
            return _result(git, "git")

    for directory in _candidates(start, ceiling):
        marker = _marker_in(directory)
        if marker is not None:
            return _result(directory, "marker", marker)

    if script is None:
        return _result(cwd, "cwd")
    if cwd != start and _is_ancestor(cwd, start) and _strictly_below(cwd, ceiling):
        return _result(cwd, "cwd")

    logger.warning(
        "scitex-session: no project root found for %s (no git repository, no "
        "project marker such as pyproject.toml or config/ + scripts/ in its "
        "ancestors, and the working directory %s does not contain it); using "
        "the script's directory %s. Files outside it are recorded as EXTERNAL "
        "to the project. Set $%s, pass @session(project_root=...), add a "
        "marker at the project directory, or run from it.",
        script,
        cwd,
        start,
        PROJECT_ROOT_ENV,
    )
    return _result(start, "script_dir")


def _result(root: Path, source: str, marker: Optional[str] = None) -> Dict[str, Any]:
    return {
        "project_root": str(root),
        "project_root_source": source,
        "project_root_marker": marker,
    }


def _explicit(value: Any, source: str, what: str, start: Path) -> Dict[str, Any]:
    path = Path(os.path.expanduser(str(value))).resolve()
    if not path.is_dir():
        raise ValueError(f"{what} {str(value)!r} is not an existing directory")
    if not _is_ancestor(path, start):
        logger.warning(
            "scitex-session: %s %s does not contain %s; the entry script and "
            "files beside it are recorded as EXTERNAL to the project",
            what,
            path,
            start,
        )
    return _result(path, source)


def _home(environ: Dict[str, str]) -> Optional[Path]:
    home = environ.get("HOME") or os.path.expanduser("~")
    if not home or home == "~":
        return None
    return Path(home).resolve()


def _ceiling(start: Path, environ: Dict[str, str]) -> Path:
    """The directory the upward search must stay strictly below."""
    home = _home(environ)
    if home is not None and home != Path(home.anchor) and _is_ancestor(home, start):
        return home
    return Path(start.anchor)


def _candidates(start: Path, ceiling: Path) -> Iterator[Path]:
    """``start`` and its ancestors strictly below ``ceiling``, same filesystem."""
    if not _strictly_below(start, ceiling):
        return
    try:
        device = start.stat().st_dev
    except OSError:
        return
    current = start
    while _strictly_below(current, ceiling):
        yield current
        parent = current.parent
        try:
            if parent.stat().st_dev != device:
                return  # mount point: the parent is another filesystem
        except OSError:
            return
        current = parent


def _marker_in(directory: Path) -> Optional[str]:
    for marker in PROJECT_MARKERS:
        if isinstance(marker, tuple):
            if all((directory / name).is_dir() for name in marker):
                return " + ".join(f"{name}/" for name in marker)
        elif os.path.lexists(directory / marker):
            return marker
    return None


def _is_ancestor(ancestor: Path, path: Path) -> bool:
    return ancestor == path or ancestor in path.parents


def _strictly_below(path: Path, ceiling: Path) -> bool:
    return path != ceiling and ceiling in path.parents


# EOF
