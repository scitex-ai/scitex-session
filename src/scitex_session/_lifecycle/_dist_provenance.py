#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_dist_provenance.py
"""Where an imported distribution came from, and whether its files are intact.

``packages`` records ``{distribution: version}``. A name and a version do not
identify the code that ran: a dependency patched and rebuilt locally, installed
from a local directory, or edited in place after installation reports the
same version as the release on the package index. A replay that installs the
index release then runs different code. This module records, per imported
distribution, what is needed to tell those cases apart (field
``package_provenance`` of the run metadata):

``source``
    How it was installed, from PEP 610 ``direct_url.json``: ``"index"`` (no
    ``direct_url.json``: a package index or a ``--find-links`` wheelhouse),
    ``"editable"``, ``"local_dir"``, ``"local_archive"`` (a ``file://`` wheel
    or sdist), ``"remote_archive"`` (an ``https://`` archive) or ``"vcs"``.
    ``url`` / ``vcs`` / ``commit_id`` / ``archive_hash`` carry the rest of the
    PEP 610 record when present.
``installer``
    The ``INSTALLER`` file (``pip``, ``uv``, ``conda``, ...), when present.
``fingerprint``
    ``"sha256:<hex>"`` over the distribution's ``RECORD`` entries
    (``path,hash`` lines, sorted), leaving out what the installer writes
    rather than what the wheel ships: byte-compiled ``.pyc`` files,
    the ``INSTALLER`` / ``REQUESTED`` / ``direct_url.json`` / ``RECORD``
    files, and files installed outside the install root (``../bin/...``
    console scripts whose shebang names the interpreter). Every remaining
    entry carries the hash the wheel builder computed for that file and the
    installer checked on install, so the fingerprint is a function of the
    wheel's content alone: the same wheel gives the same fingerprint under
    pip and uv, on any machine; a rebuilt wheel with one changed file gives
    a different one. It costs one read of a small text file — hashing the
    installed files themselves would read hundreds of MB for a scientific
    stack. ``None`` when there is no ``RECORD`` (egg-info / some conda
    installs) or for an editable install (its ``RECORD`` lists only a hook).
``modified_files``
    Installed files whose content no longer matches their ``RECORD`` hash:
    an index install edited in place keeps its version and its fingerprint,
    so this is the only trace of the edit. Checked cheaply: one ``stat`` per
    file; a file is hashed only when its size differs from ``RECORD`` or its
    mtime is newer than ``RECORD`` (which the installer writes last). A
    deleted file is listed too. At most :data:`MAX_MODIFIED_LISTED` paths;
    ``modified_files_truncated`` gives the full count beyond that.

A distribution is *reproducible from its record* when ``source`` is
``"index"`` and ``modified_files`` is empty; :func:`non_reproducible_reason`
says why otherwise. Whether its content matches a later environment is a
fingerprint comparison (scitex-clew does it at ``verify``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

__all__ = [
    "FINGERPRINT_SCHEME",
    "MAX_MODIFIED_LISTED",
    "distribution_provenance",
    "install_source",
    "non_reproducible_reason",
    "record_fingerprint",
]

MAX_MODIFIED_LISTED = 20
FINGERPRINT_SCHEME = "record-sha256-v1"
# Written by the installer, not shipped by the wheel: never part of the
# content fingerprint (they differ between pip and uv, or between installs).
_INSTALLER_FILES = frozenset({"INSTALLER", "REQUESTED", "direct_url.json", "RECORD"})
# Copied with the 1 MiB buffer ``hashlib.file_digest`` uses.
_CHUNK = 1 << 20


def install_source(direct_url: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """PEP 610 ``direct_url.json`` (parsed) -> ``{"source", ...}``."""
    if not direct_url:
        return {"source": "index"}
    url = str(direct_url.get("url", ""))
    out: Dict[str, Any] = {"url": url}
    vcs = direct_url.get("vcs_info")
    if vcs is not None:
        out["source"] = "vcs"
        out["vcs"] = vcs.get("vcs")
        out["commit_id"] = vcs.get("commit_id")
        if vcs.get("requested_revision"):
            out["requested_revision"] = vcs["requested_revision"]
        return out
    archive = direct_url.get("archive_info")
    if archive is not None:
        out["source"] = (
            "local_archive" if urlparse(url).scheme == "file" else "remote_archive"
        )
        hashes = archive.get("hashes") or {}
        digest = archive.get("hash") or (
            f"sha256={hashes['sha256']}" if "sha256" in hashes else None
        )
        if digest:
            out["archive_hash"] = digest
        return out
    dir_info = direct_url.get("dir_info") or {}
    out["source"] = "editable" if dir_info.get("editable") else "local_dir"
    return out


def _record_rows(record: str) -> List[Tuple[str, str, str]]:
    """``(path, hash, size)`` per ``RECORD`` line.

    ``RECORD`` is CSV, but only the path can contain a comma (then it is
    quoted); hash and size never do, so splitting from the right is exact
    and several times faster than :mod:`csv` on a 10k-line ``RECORD``.
    """
    rows = []
    for line in record.splitlines():
        parts = line.rsplit(",", 2)
        if len(parts) != 3:
            continue
        path, digest, size = parts
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1].replace('""', '"')
        rows.append((path, digest, size))
    return rows


def _fingerprinted(path: str) -> bool:
    if path.startswith("..") or path.startswith("/"):
        return False  # outside the install root: console scripts, data files
    parts = path.split("/")
    if "__pycache__" in parts or path.endswith((".pyc", ".pyo")):
        return False
    return not (
        len(parts) == 2
        and parts[0].endswith(".dist-info")
        and parts[1] in _INSTALLER_FILES
    )


def record_fingerprint(record: str) -> Optional[str]:
    """``"sha256:<hex>"`` over the wheel-shipped ``RECORD`` entries (see module doc)."""
    lines = sorted(
        f"{path},{digest}"
        for path, digest, _size in _record_rows(record)
        if digest and _fingerprinted(path)
    )
    if not lines:
        return None
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _file_digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return base64.urlsafe_b64encode(h.digest()).rstrip(b"=").decode("ascii")


def _modified_files(dist: Any, record: str) -> List[str]:
    """``RECORD`` paths whose installed content no longer matches (see module doc)."""
    base = str(dist.locate_file(""))
    record_path = getattr(dist, "_path", None)
    try:
        record_mtime = os.stat(Path(record_path) / "RECORD").st_mtime_ns
    except (OSError, TypeError):
        record_mtime = None
    modified: List[str] = []
    for path, recorded, size in _record_rows(record):
        if not recorded or not _fingerprinted(path):
            continue
        full = os.path.join(base, path)
        try:
            st = os.stat(full)
        except FileNotFoundError:
            modified.append(path)
            continue
        size_differs = size.isdigit() and int(size) != st.st_size
        newer = record_mtime is not None and st.st_mtime_ns > record_mtime
        if not (size_differs or newer):
            continue
        algorithm, _, expected = recorded.partition("=")
        try:
            if size_differs or _file_digest(full, algorithm) != expected:
                modified.append(path)
        except (OSError, ValueError):
            modified.append(path)
    return sorted(modified)


def _read_json(dist: Any, name: str) -> Optional[Dict[str, Any]]:
    raw = dist.read_text(name)
    if not raw:
        return None
    value = json.loads(raw)
    return value if isinstance(value, dict) else None


def distribution_provenance(dist: Any) -> Dict[str, Any]:
    """Install provenance + content fingerprint of one installed distribution.

    ``dist`` is an :class:`importlib.metadata.Distribution`. See the module
    docstring for the fields.
    """
    info: Dict[str, Any] = {"version": dist.version}
    info.update(install_source(_read_json(dist, "direct_url.json")))
    installer = (dist.read_text("INSTALLER") or "").strip()
    if installer:
        info["installer"] = installer
    record = dist.read_text("RECORD")
    if not record or info["source"] == "editable":
        info["fingerprint"] = None
        info["modified_files"] = []
        return info
    info["fingerprint"] = record_fingerprint(record)
    info["fingerprint_scheme"] = FINGERPRINT_SCHEME
    modified = _modified_files(dist, record)
    info["modified_files"] = modified[:MAX_MODIFIED_LISTED]
    if len(modified) > MAX_MODIFIED_LISTED:
        info["modified_files_truncated"] = len(modified)
    return info


def non_reproducible_reason(info: Dict[str, Any]) -> Optional[str]:
    """Why a recorded distribution cannot be re-installed from its record.

    ``None`` for an index install whose files were intact.
    """
    source = info.get("source")
    if source not in (None, "index"):
        where = info.get("url") or "?"
        if source == "vcs" and info.get("commit_id"):
            where = f"{where}@{info['commit_id']}"
        return (
            f"installed from {source.replace('_', ' ')} ({where}), not a package index"
        )
    modified = info.get("modified_files") or []
    if modified:
        total = info.get("modified_files_truncated") or len(modified)
        return (
            f"{total} installed file(s) edited after installation "
            f"(e.g. {modified[0]}); the index release differs"
        )
    return None


# EOF
