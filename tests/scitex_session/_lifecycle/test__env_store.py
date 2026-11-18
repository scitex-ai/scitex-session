#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__env_store.py

"""Environment provenance stored once per environment, referenced per run.

Real files and real provenance collected from this interpreter's imported
distributions — no mocks. The end-to-end class runs real ``@session``
sessions in a subprocess.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_session._lifecycle._env_store import (
    ENV_STORE_PARTS,
    REF_FORMAT,
    canonical_provenance_bytes,
    externalize_package_provenance,
    load_run_provenance,
    resolve_package_provenance,
    store_package_provenance,
)
from scitex_session._lifecycle._provenance import collect_run_metadata


@pytest.fixture
def real_meta(tmp_path_factory):
    """Run metadata collected for a real script in this process."""
    root = tmp_path_factory.mktemp("proj")
    (root / "pyproject.toml").write_text("")
    script = root / "s.py"
    script.write_text("")
    meta = collect_run_metadata(str(script), project_root=root)
    assert meta["package_provenance"], "this interpreter imported distributions"
    return meta


class TestStore:
    def test_file_content_hashes_to_the_reference(self, real_meta, tmp_path):
        # Arrange
        provenance = real_meta["package_provenance"]
        # Act
        ref = store_package_provenance(provenance, tmp_path)
        # Assert
        data = Path(ref["abspath"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == ref["sha256"]

    def test_file_lives_in_the_project_environment_store(self, real_meta, tmp_path):
        # Arrange
        provenance = real_meta["package_provenance"]
        # Act
        ref = store_package_provenance(provenance, tmp_path)
        # Assert
        assert Path(ref["abspath"]) == tmp_path.joinpath(
            *ENV_STORE_PARTS, f"{ref['sha256']}.json"
        )

    def test_same_provenance_is_stored_once(self, real_meta, tmp_path):
        # Arrange
        provenance = real_meta["package_provenance"]
        # Act
        store_package_provenance(provenance, tmp_path)
        store_package_provenance(dict(provenance), tmp_path)
        # Assert
        assert len(list(tmp_path.joinpath(*ENV_STORE_PARTS).iterdir())) == 1

    def test_different_provenance_gets_its_own_file(self, real_meta, tmp_path):
        # Arrange
        other = dict(real_meta["package_provenance"])
        other.pop(sorted(other)[0])
        # Act
        a = store_package_provenance(real_meta["package_provenance"], tmp_path)
        b = store_package_provenance(other, tmp_path)
        # Assert
        assert a["sha256"] != b["sha256"]

    def test_hash_does_not_depend_on_key_order(self, real_meta):
        # Arrange
        prov = real_meta["package_provenance"]
        reversed_order = dict(reversed(list(prov.items())))
        # Act
        same = canonical_provenance_bytes(prov) == canonical_provenance_bytes(
            reversed_order
        )
        # Assert
        assert same

    def test_a_corrupted_store_file_is_rewritten(self, real_meta, tmp_path):
        # Arrange
        ref = store_package_provenance(real_meta["package_provenance"], tmp_path)
        Path(ref["abspath"]).write_text("{}")
        # Act
        store_package_provenance(real_meta["package_provenance"], tmp_path)
        # Assert
        data = Path(ref["abspath"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == ref["sha256"]


class TestExternalize:
    def test_record_carries_a_reference_instead_of_the_inline_set(self, real_meta):
        # Arrange
        meta = dict(real_meta)
        # Act
        out = externalize_package_provenance(meta)
        # Assert
        assert (
            "package_provenance" in out,
            out["package_provenance_ref"]["format"],
        ) == (
            False,
            REF_FORMAT,
        )

    def test_input_metadata_is_not_modified(self, real_meta):
        # Arrange
        before = json.dumps(real_meta, sort_keys=True)
        # Act
        externalize_package_provenance(real_meta)
        # Assert
        assert json.dumps(real_meta, sort_keys=True) == before

    def test_round_trip_restores_the_identical_provenance(self, real_meta):
        # Arrange
        out = externalize_package_provenance(real_meta)
        # Act
        resolved = resolve_package_provenance(out)
        # Assert
        assert resolved == real_meta["package_provenance"]

    def test_unwritable_store_keeps_the_provenance_inline(self, real_meta, tmp_path):
        # Arrange: the store directory's parent is a regular file
        (tmp_path / ".scitex").write_text("not a directory")
        meta = dict(real_meta, project_root=str(tmp_path))
        # Act
        out = externalize_package_provenance(meta)
        # Assert
        assert (
            out["package_provenance"] == real_meta["package_provenance"]
            and "package_provenance_store_error" in out
        )


class TestResolve:
    def test_old_inline_record_reads_unchanged(self, real_meta):
        # Arrange
        old_record = dict(real_meta)
        # Act
        resolved = resolve_package_provenance(old_record)
        # Assert
        assert resolved == real_meta["package_provenance"]

    def test_record_without_provenance_resolves_to_none(self):
        # Arrange
        pre_050_record = {"packages": {}}
        # Act
        resolved = resolve_package_provenance(pre_050_record)
        # Assert
        assert resolved is None

    def test_missing_environment_file_raises(self, real_meta, tmp_path):
        # Arrange
        meta = dict(real_meta, project_root=str(tmp_path))
        out = externalize_package_provenance(meta)
        Path(out["package_provenance_ref"]["abspath"]).unlink()
        digest = out["package_provenance_ref"]["sha256"]
        # Act
        call = lambda: resolve_package_provenance(out)  # noqa: E731
        # Assert
        with pytest.raises(FileNotFoundError, match=digest):
            call()

    def test_tampered_environment_file_raises(self, real_meta, tmp_path):
        # Arrange
        meta = dict(real_meta, project_root=str(tmp_path))
        out = externalize_package_provenance(meta)
        Path(out["package_provenance_ref"]["abspath"]).write_text("{}")
        # Act
        call = lambda: resolve_package_provenance(out)  # noqa: E731
        # Assert
        with pytest.raises(ValueError, match="do not match"):
            call()

    def test_moved_project_resolves_from_the_run_directory(self, real_meta, tmp_path):
        # Arrange: record written in old/, project then moved to new/
        old = tmp_path / "old"
        old.mkdir()
        out = externalize_package_provenance(dict(real_meta, project_root=str(old)))
        new = tmp_path / "new"
        old.rename(new)
        run_dir = new / "scripts" / "s_out" / "FINISHED_SUCCESS" / "ID"
        run_dir.mkdir(parents=True)
        # Act
        resolved = resolve_package_provenance(out, run_dir=run_dir)
        # Assert
        assert resolved == real_meta["package_provenance"]


PROBE = """\
import scitex_io as sio
import scitex_session as stx_session


@stx_session.session(verbose=False)
def main(CONFIG=stx_session.INJECTED):
    sio.save(sio.load("data/a.csv"), "b.csv")
    return 0


if __name__ == "__main__":
    main()
"""


@pytest.fixture(scope="class")
def two_sessions(tmp_path_factory):
    """Two real sessions of the same script in one project (same environment).

    The child gets an explicit environment; ``SCITEX_STORE_DSN`` must be set
    because an installed provenance observer (scitex-clew) records into it.
    """
    dsn = os.environ.get("SCITEX_STORE_DSN")
    if not dsn:
        pytest.skip("SCITEX_STORE_DSN unset: an installed observer would record")
    root = tmp_path_factory.mktemp("e2e")
    (root / "scripts").mkdir()
    (root / "data").mkdir()
    (root / "pyproject.toml").write_text("")
    (root / "data" / "a.csv").write_text("x\n1\n2\n")
    (root / "scripts" / "probe.py").write_text(PROBE)
    env = {
        "HOME": os.environ["HOME"],
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TMPDIR": str(root),
        "SCITEX_STORE_DSN": dsn,
    }
    for _ in range(2):
        subprocess.run(
            [sys.executable, "scripts/probe.py"],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            timeout=300,
        )
    runs = sorted((root / "scripts" / "probe_out" / "FINISHED_SUCCESS").iterdir())
    return root, runs


class TestRealSessions:
    def test_two_runs_share_one_environment_file(self, two_sessions):
        # Arrange
        root, _runs = two_sessions
        # Act
        files = list(root.joinpath(*ENV_STORE_PARTS).glob("*.json"))
        # Assert
        assert len(files) == 1

    def test_run_record_holds_no_inline_provenance(self, two_sessions):
        # Arrange
        _root, runs = two_sessions
        # Act
        text = (runs[0] / "CONFIGS" / "CONFIG.yaml").read_text()
        # Assert
        assert ("package_provenance_ref" in text, "fingerprint" in text) == (
            True,
            False,
        )

    def test_run_record_resolves_to_full_provenance(self, two_sessions):
        # Arrange
        _root, runs = two_sessions
        # Act
        record = load_run_provenance(runs[-1])
        # Assert
        assert set(record["package_provenance"]) == set(record["packages"])


# EOF
