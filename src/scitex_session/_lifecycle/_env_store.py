#!/usr/bin/env python3
# File: ./src/scitex_session/_lifecycle/_env_store.py
"""Environment provenance stored once per environment, referenced per run.

``package_provenance`` (see :mod:`._dist_provenance`) describes every imported
distribution: install source, wheel fingerprint, files edited since
installation. For a scientific stack it is tens of KB, and it is the same for
every session run in the same environment. Written inline into each run's
``CONFIGS/CONFIG.yaml`` and ``CONFIGS/CONFIG.pkl`` it made a one-session run
directory ~4.5x larger (7.8 KB -> 36 KB) while carrying no new information
after the first run.

The run record on disk now holds a reference instead:

``package_provenance_ref``
    ``{"format", "sha256", "distributions", "path", "abspath"}``. ``sha256``
    is the SHA-256 of the canonical JSON of the provenance mapping (sorted
    keys, no whitespace, UTF-8); the referenced file holds exactly those
    bytes, so ``sha256sum <file>`` equals the reference. ``path`` is the file
    relative to the run's ``project_root``; ``abspath`` is where it was
    written.

The file lives at ``<project_root>/.scitex/session/environments/<sha256>.json``
(:data:`ENV_STORE_PARTS`), is written once (atomically) and reused by every
later run whose provenance set is identical. Nothing is dropped: the set is
stored whole, and a reader resolves the reference back to the same mapping
(:func:`resolve_package_provenance`, :func:`load_run_provenance`), verifying
the hash. A run record written before this change (``package_provenance``
inline) reads unchanged.

When the store cannot be written (read-only project, full disk) the record
keeps the provenance inline and says why in ``package_provenance_store_error``
— the information is never lost to save space.

Only the on-disk run record changes. The metadata handed to provenance
observers (scitex-clew stores it as the run's ``metadata``) still carries
``package_provenance`` inline, so observers need no reader change.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from scitex_logging import getLogger

logger = getLogger(__name__)

#: Store location under the project root.
ENV_STORE_PARTS = (".scitex", "session", "environments")

#: ``format`` tag of a reference (and of the scheme its hash follows).
REF_FORMAT = "scitex-session/package-provenance/v1"

PathLike = Union[str, os.PathLike]


def canonical_provenance_bytes(provenance: Mapping[str, Any]) -> bytes:
    """Canonical JSON bytes of a provenance mapping (the hashed content)."""
    return json.dumps(
        provenance,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def env_store_dir(project_root: PathLike) -> Path:
    """``<project_root>/.scitex/session/environments``."""
    return Path(project_root).joinpath(*ENV_STORE_PARTS)


def store_package_provenance(
    provenance: Mapping[str, Any], project_root: PathLike
) -> Dict[str, Any]:
    """Write ``provenance`` once to the project's environment store.

    Returns the reference (see the module docstring). An existing file with
    the right content is reused; one with other content (a torn or edited
    file) is replaced, since its name promises the hash.
    """
    data = canonical_provenance_bytes(provenance)
    digest = hashlib.sha256(data).hexdigest()
    root = Path(project_root)
    target = env_store_dir(root) / f"{digest}.json"
    if not _holds(target, digest):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{digest[:16]}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return {
        "format": REF_FORMAT,
        "sha256": digest,
        "distributions": len(provenance),
        "path": str(Path(*ENV_STORE_PARTS, target.name)),
        "abspath": str(target),
    }


def externalize_package_provenance(meta: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy of run metadata ``meta`` with ``package_provenance`` stored once
    and replaced by ``package_provenance_ref``.

    ``meta`` is not modified. Metadata without ``package_provenance`` (or
    without a ``project_root`` to store it under) is returned as a copy. A
    store failure keeps the provenance inline and records the error in
    ``package_provenance_store_error``.
    """
    out = dict(meta)
    provenance = out.get("package_provenance")
    if not isinstance(provenance, Mapping):
        return out
    root = out.get("project_root")
    if not root:
        out["package_provenance_store_error"] = (
            "no project_root in the run metadata; provenance kept inline"
        )
        return out
    try:
        ref = store_package_provenance(provenance, root)
    except Exception as exc:  # noqa: BLE001 - recorded + warned, kept inline
        out["package_provenance_store_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "scitex-session: could not write the environment store under %s "
            "(%s); package_provenance stays inline in this run's record",
            root,
            out["package_provenance_store_error"],
        )
        return out
    del out["package_provenance"]
    out["package_provenance_ref"] = ref
    return out


def resolve_package_provenance(
    meta: Mapping[str, Any], run_dir: Optional[PathLike] = None
) -> Optional[Dict[str, Any]]:
    """The ``package_provenance`` of a run record, inline or by reference.

    Returns ``None`` when the record has neither (a run recorded before
    scitex-session 0.5.0). A reference is looked up, in order, under the
    record's ``project_root``, at its ``abspath``, and in the
    ``.scitex/session/environments`` of every ancestor of ``run_dir`` (a
    project that was moved or copied). The file's hash must equal the
    reference.

    Raises
    ------
    FileNotFoundError
        The referenced environment file is in none of those places.
    ValueError
        A file was found but its content does not match the reference.
    """
    inline = meta.get("package_provenance")
    if isinstance(inline, Mapping):
        return dict(inline)
    ref = meta.get("package_provenance_ref")
    if not isinstance(ref, Mapping):
        return None
    digest = str(ref.get("sha256") or "")
    name = f"{digest}.json"
    candidates = []
    if meta.get("project_root"):
        candidates.append(env_store_dir(meta["project_root"]) / name)
    if ref.get("abspath"):
        candidates.append(Path(ref["abspath"]))
    if run_dir is not None:
        start = Path(run_dir).resolve()
        candidates += [env_store_dir(d) / name for d in (start, *start.parents)]
    seen = set()
    mismatched = []
    for path in candidates:
        if path in seen or not path.is_file():
            seen.add(path)
            continue
        seen.add(path)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            mismatched.append(str(path))
            continue
        return json.loads(data.decode("utf-8"))
    if mismatched:
        raise ValueError(
            f"environment file(s) {mismatched} do not match package_provenance_ref "
            f"sha256 {digest}"
        )
    raise FileNotFoundError(
        f"package_provenance_ref {digest}: no {name} under "
        f"{sorted(str(p) for p in seen)}"
    )


def load_run_provenance(run_dir: PathLike) -> Dict[str, Any]:
    """A finished run's ``_PROVENANCE`` record with ``package_provenance``
    resolved inline (whatever version wrote it).

    ``run_dir`` is the run's directory (``.../FINISHED_SUCCESS/<ID>/``); its
    ``CONFIGS/CONFIG.yaml`` is read.
    """
    import yaml

    config_path = Path(run_dir) / "CONFIGS" / "CONFIG.yaml"
    with open(config_path, encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    record = dict(config.get("_PROVENANCE") or {})
    provenance = resolve_package_provenance(record, run_dir=run_dir)
    if provenance is not None:
        record["package_provenance"] = provenance
    return record


def _holds(path: Path, digest: str) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except FileNotFoundError:
        return False


# EOF
