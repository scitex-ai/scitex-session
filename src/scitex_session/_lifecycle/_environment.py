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
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import unquote, urlparse

_INSTALLED_MARKERS = ("site-packages", "dist-packages")
_MISSING = object()


def _module_dict(module: Any) -> Optional[Dict[str, Any]]:
    """A module's ``__dict__`` read WITHOUT running its attribute hooks.

    ``object.__getattribute__`` bypasses the ``__getattribute__`` of a
    not-yet-loaded ``importlib.util.LazyLoader`` module and the module-level
    ``__getattr__`` of moved-module aliases, so inspecting ``sys.modules``
    never imports or executes anything. (A plain ``getattr(module,
    "__file__")`` did: it ran every lazily-imported module in the
    environment — seconds of start-up — and fired the aliases' deprecation
    warnings into every run log.) ``None`` for an object without a
    ``__dict__``.
    """
    try:
        return object.__getattribute__(module, "__dict__")
    except (AttributeError, TypeError):
        return None


def _module_attr(module: Any, name: str) -> Any:
    """``getattr(module, name, None)`` without triggering attribute hooks.

    A module that has not been executed (lazy) still carries its import
    attributes (``__file__``, ``__spec__``, ``__path__``) in its dict, so the
    answer is the same as after loading it. An attribute that is not in the
    dict is answered ``None`` rather than computed by a hook; objects with no
    ``__dict__`` fall back to ``getattr``.
    """
    namespace = _module_dict(module)
    if namespace is None:
        return getattr(module, name, None)
    value = namespace.get(name, _MISSING)
    return None if value is _MISSING else value


class _Resolver:
    """``Path(p).resolve()`` with the containing directory's realpath cached.

    Thousands of imported modules live in a few hundred directories; resolving
    each file from scratch (one ``lstat`` per path component) dominated the
    environment capture. ``realpath(dir) / name`` is the same path unless
    ``name`` itself is a symlink, which is checked (one ``lstat``). One
    instance per collection, so nothing is cached across a run.
    """

    def __init__(self) -> None:
        self._dirs: Dict[str, str] = {}

    def __call__(self, path: Any) -> Path:
        absolute = os.path.abspath(os.fspath(path))
        head, tail = os.path.split(absolute)
        real_head = self._dirs.get(head)
        if real_head is None:
            real_head = self._dirs[head] = os.path.realpath(head)
        joined = os.path.join(real_head, tail)
        if tail and os.path.islink(joined):
            joined = os.path.realpath(joined)
        return Path(joined)


def _module_file(module: Any, resolve: Optional[_Resolver] = None) -> Optional[Path]:
    path = _module_attr(module, "__file__")
    if not path or not str(path).endswith(".py"):
        return None
    p = (resolve or _Resolver())(path)
    return p if p.is_file() else None


def _module_location(
    module: Any, resolve: Optional[_Resolver] = None
) -> Optional[Path]:
    """Where a module was loaded from: its file (any suffix) or package dir."""
    resolve = resolve or _Resolver()
    path = _module_attr(module, "__file__")
    if path:
        return resolve(path)
    for entry in list(_module_attr(module, "__path__") or ()):
        return resolve(entry)  # namespace package: its first portion
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
    spec = _module_attr(module, "__spec__")
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


#: ``(environment key, index)`` of the last :func:`_distribution_index` scan.
#: Start and close of a session both need the index; close reuses the start
#: scan unless the installed set may have changed (see :func:`_index_key`).
_INDEX_CACHE: Optional[Tuple[Any, Any]] = None


def _index_key() -> Tuple[Tuple[str, Optional[int]], ...]:
    """Every ``sys.path`` entry with its directory mtime.

    Installing, upgrading or removing a distribution creates or deletes its
    ``*.dist-info`` directory (and an editable install's ``.pth``) directly
    inside a ``sys.path`` directory, which changes that directory's mtime;
    so does any change to ``sys.path`` itself. An unchanged key therefore
    means the scan would find the same distributions.
    """
    key = []
    for entry in sys.path:
        try:
            mtime: Optional[int] = os.stat(entry or ".").st_mtime_ns
        except OSError:
            mtime = None
        key.append((entry, mtime))
    return tuple(key)


def _cached_distribution_index() -> Tuple[Dict[str, List[Any]], List[Tuple[Path, Any]]]:
    """:func:`_distribution_index`, rescanned only when :func:`_index_key` changed."""
    global _INDEX_CACHE
    key = _index_key()
    if _INDEX_CACHE is None or _INDEX_CACHE[0] != key:
        _INDEX_CACHE = (key, _distribution_index())
    return _INDEX_CACHE[1]


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
    return _attribute_modules(modules, project_root, _cached_distribution_index())


#: ``id(dist) -> (dist, name)``: METADATA parsing is the expensive part of
#: naming an owner, and the same distributions are named at start and close.
#: The distribution is kept in the value so an id is never reused.
_DIST_NAMES: Dict[int, Tuple[Any, Optional[str]]] = {}


def _dist_name(dist: Any) -> Optional[str]:
    """``Name`` from a distribution's METADATA, parsed once per process."""
    cached = _DIST_NAMES.get(id(dist))
    if cached is None or cached[0] is not dist:
        cached = _DIST_NAMES[id(dist)] = (dist, dist.metadata["Name"])
    return cached[1]


def _attribute_modules(
    modules: Mapping[str, Any],
    project_root: Optional[Path],
    index: Tuple[Dict[str, List[Any]], List[Tuple[Path, Any]]],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """One attribution pass of :func:`_imported_distributions` over ``index``."""
    by_module, editable = index
    resolve = _Resolver()
    interpreter = _interpreter_roots()
    search_roots = [
        resolve(entry) for entry in sys.path if entry and _is_installed(Path(entry))
    ]
    dists: Dict[str, Any] = {}
    unresolved: Dict[str, str] = {}
    bases: Dict[int, Path] = {}

    def base_of(dist: Any) -> Path:
        if id(dist) not in bases:
            bases[id(dist)] = _dist_base(dist)
        return bases[id(dist)]

    def add(owners: Iterable[Any]) -> None:
        for dist in owners:
            name = _dist_name(dist)
            if name:  # first on sys.path wins, as on import
                dists.setdefault(name, dist)

    for name in sorted({key.split(".", 1)[0] for key in list(modules)}):
        if name in _NOT_A_MODULE:
            continue
        module = modules.get(name)
        location = _module_location(module, resolve) if module is not None else None
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
        owners = [d for d in candidates if _under(location, [base_of(d)])] or [
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
    resolve = _Resolver()
    hashes: Dict[str, str] = {}
    for module in list(modules.values()):
        path = _module_file(module, resolve)
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
