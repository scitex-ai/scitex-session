#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_environment.py
"""The software a run imported: distributions, unresolved imports, local code.

Collectors behind the ``packages`` / ``unresolved_imports`` /
``local_modules`` fields of :mod:`._provenance` (see its docstring for the
field semantics).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import unquote, urlparse

_INSTALLED_MARKERS = ("site-packages", "dist-packages")


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
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Distributions behind every imported top-level module.

    Returns ``(dists, unresolved)``: ``{name: importlib.metadata.Distribution}``
    (the copy Python imports when several are installed) and
    ``{module: location}`` for imported modules that are not stdlib, not
    project code and not attributable to any installed distribution.
    """
    by_module, editable = _distribution_index()
    interpreter = _interpreter_roots()
    search_roots = [
        Path(entry).resolve() for entry in sys.path if entry and _is_installed(Path(entry))
    ]
    dists: Dict[str, Any] = {}
    unresolved: Dict[str, str] = {}

    def add(owners: Iterable[Any]) -> None:
        for dist in owners:
            name = dist.metadata["Name"]
            if name:  # first on sys.path wins, as on import
                dists.setdefault(name, dist)

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
    return dict(sorted(dists.items())), unresolved


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
