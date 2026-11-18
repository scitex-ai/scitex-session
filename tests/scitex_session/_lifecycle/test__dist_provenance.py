#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__dist_provenance.py

"""Install provenance + content fingerprint of imported distributions.

Real distributions installed by a real ``pip`` (a fresh venv per test
session, offline): a tiny package with an in-tree PEP 517 backend is built
into wheels and installed from a wheelhouse (index-like), from a local
directory, from a wheel file and as an editable — no mocks, no network.
Subprocesses get an explicit environment, never ``os.environ``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import distributions
from pathlib import Path

import pytest

from scitex_session._lifecycle._dist_provenance import (
    distribution_provenance,
    non_reproducible_reason,
    record_fingerprint,
)
from scitex_session._lifecycle._provenance import (
    collect_close_metadata,
    collect_run_metadata,
)

NAME = "tinydist_probe"

BACKEND = """
import base64, hashlib, os, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME, VERSION = "tinydist_probe", "1.0.0"


def _hash(data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return "sha256=" + digest.decode()


def _wheel(wheel_directory, files):
    info = f"{NAME}-{VERSION}.dist-info"
    files = dict(files)
    files[f"{info}/METADATA"] = (
        f"Metadata-Version: 2.1\\nName: {NAME}\\nVersion: {VERSION}\\n".encode()
    )
    files[f"{info}/WHEEL"] = (
        b"Wheel-Version: 1.0\\nGenerator: probe\\nRoot-Is-Purelib: true\\n"
        b"Tag: py3-none-any\\n"
    )
    files[f"{info}/top_level.txt"] = f"{NAME}\\n".encode()
    record = "".join(f"{p},{_hash(d)},{len(d)}\\n" for p, d in files.items())
    files[f"{info}/RECORD"] = (record + f"{info}/RECORD,,\\n").encode()
    name = f"{NAME}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(os.path.join(wheel_directory, name), "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return name


def _sources():
    pkg = os.path.join(HERE, NAME)
    return {
        f"{NAME}/{f}": open(os.path.join(pkg, f), "rb").read()
        for f in sorted(os.listdir(pkg))
        if f.endswith(".py")
    }


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _wheel(wheel_directory, _sources())


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _wheel(wheel_directory, {f"{NAME}.pth": (HERE + "\\n").encode()})


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_editable(config_settings=None):
    return []
"""

PYPROJECT = """[build-system]
requires = []
build-backend = "probe_backend"
backend-path = ["."]
"""


def _env(home: Path) -> dict:
    """Explicit subprocess environment (never ``os.environ``)."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PIP_NO_INPUT": "1",
    }


def _make_project(root: Path, body: str) -> Path:
    (root / NAME).mkdir(parents=True)
    (root / NAME / "__init__.py").write_text(body)
    (root / "probe_backend.py").write_text(BACKEND)
    (root / "pyproject.toml").write_text(PYPROJECT)
    return root


def _build_wheel(project: Path, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        f"probe_backend_{id(project)}", project / "probe_backend.py"
    )
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return out / backend.build_wheel(str(out))


@pytest.fixture(scope="session")
def pip_env(tmp_path_factory):
    """A fresh venv with pip (offline ``ensurepip``) and two source trees."""
    base = tmp_path_factory.mktemp("distprov")
    venv = base / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        check=True,
        capture_output=True,
        env=_env(base),
        timeout=300,
    )
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    release = _make_project(base / "release", "VALUE = 1\n")
    patched = _make_project(base / "patched", "VALUE = 2  # local patch\n")
    return {
        "base": base,
        "python": python,
        "release": release,
        "patched": patched,
        "release_wheels": _build_wheel(release, base / "wh_release").parent,
        "patched_wheels": _build_wheel(patched, base / "wh_patched").parent,
    }


def _pip_install(env, site: Path, *args: str) -> Path:
    """Install into ``site`` (named ``site-packages``) with the venv's pip."""
    site.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(env["python"]),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-cache-dir",
            "--disable-pip-version-check",
            "--target",
            str(site),
            *args,
        ],
        check=True,
        capture_output=True,
        env=_env(env["base"]),
        timeout=300,
    )
    return site


def _dist(site: Path):
    found = [d for d in distributions(path=[str(site)]) if d.metadata["Name"] == NAME]
    if len(found) != 1:
        raise LookupError(f"expected one {NAME} in {site}, found {found}")
    return found[0]


@pytest.fixture(scope="session")
def sites(pip_env):
    """One real ``site-packages`` per install route (never modified: tests
    that edit an installation work on a copy, see ``modified``)."""
    base = pip_env["base"]
    release, patched = pip_env["release_wheels"], pip_env["patched_wheels"]
    wheel = next(release.glob("*.whl"))

    def site(label: str, *args: str) -> Path:
        return _pip_install(pip_env, base / label / "lib" / "site-packages", *args)

    out = {
        "index": site("index", "--find-links", str(release), NAME),
        "index_no_pyc": site(
            "index_no_pyc", "--no-compile", "--find-links", str(release), NAME
        ),
        "patched_index": site("patched_index", "--find-links", str(patched), NAME),
        "local_dir": site("local_dir", str(pip_env["patched"])),
        "local_wheel": site("local_wheel", str(wheel)),
        "editable": site("editable", "-e", str(pip_env["release"])),
    }
    return out


@pytest.fixture(scope="session")
def prov(sites):
    """``distribution_provenance`` of the probe in every site."""
    return {
        label: distribution_provenance(_dist(site)) for label, site in sites.items()
    }


def _modify(site: Path, how: str) -> None:
    target = site / NAME / "__init__.py"
    if how == "edited":
        target.write_text("VALUE = 9\n")  # same size as the release
    elif how == "touched":
        future = time.time() + 60
        os.utime(target, (future, future))
    elif how == "deleted":
        target.unlink()


@pytest.fixture
def modified(sites, tmp_path):
    """Provenance of a per-test copy of the intact index install after an
    edit / touch / delete (``copytree`` keeps the installed mtimes)."""

    def make(how: str) -> dict:
        site = tmp_path / how / "lib" / "site-packages"
        shutil.copytree(sites["index"], site)
        _modify(site, how)
        return distribution_provenance(_dist(site))

    return make


class TestInstallSource:
    def test_wheelhouse_install_is_an_index_install(self, prov):
        # Arrange
        info = prov["index"]
        # Act
        source = info["source"]
        # Assert
        assert source == "index"

    def test_installer_is_recorded(self, prov):
        # Arrange
        info = prov["index"]
        # Act
        installer = info.get("installer")
        # Assert
        assert installer == "pip"

    def test_intact_index_install_is_reproducible(self, prov):
        # Arrange
        info = prov["index"]
        # Act
        reason = non_reproducible_reason(info)
        # Assert
        assert reason is None

    def test_local_directory_install_is_recorded_as_local_dir(self, prov):
        # Arrange
        info = prov["local_dir"]
        # Act
        source = info["source"]
        # Assert
        assert source == "local_dir"

    def test_local_directory_install_records_its_path(self, prov, pip_env):
        # Arrange
        expected = pip_env["patched"].resolve().as_uri()
        # Act
        url = prov["local_dir"]["url"]
        # Assert
        assert url == expected

    def test_local_directory_install_is_not_reproducible(self, prov):
        # Arrange
        info = prov["local_dir"]
        # Act
        reason = non_reproducible_reason(info) or ""
        # Assert
        assert "local dir" in reason

    def test_wheel_file_install_is_a_local_archive(self, prov):
        # Arrange
        info = prov["local_wheel"]
        # Act
        source = info["source"]
        # Assert
        assert source == "local_archive"

    def test_wheel_file_install_records_the_archive_hash(self, prov):
        # Arrange
        info = prov["local_wheel"]
        # Act
        digest = info.get("archive_hash") or ""
        # Assert
        assert digest.startswith("sha256=")

    def test_editable_install_is_recorded_as_editable(self, prov):
        # Arrange
        info = prov["editable"]
        # Act
        source = info["source"]
        # Assert
        assert source == "editable"

    def test_editable_install_has_no_fingerprint(self, prov):
        # Arrange
        info = prov["editable"]
        # Act
        fingerprint = info["fingerprint"]
        # Assert
        assert fingerprint is None


class TestFingerprint:
    def test_fingerprint_is_a_sha256(self, prov):
        # Arrange
        info = prov["index"]
        # Act
        fingerprint = info["fingerprint"]
        # Assert
        assert fingerprint.startswith("sha256:")

    def test_byte_compiled_and_plain_installs_share_the_fingerprint(self, prov, sites):
        # Arrange: one install wrote .pyc files (listed in RECORD), one did not
        has_pyc = (
            any(sites["index"].rglob("*.pyc")),
            any(sites["index_no_pyc"].rglob("*.pyc")),
        )
        # Act
        same = prov["index"]["fingerprint"] == prov["index_no_pyc"]["fingerprint"]
        # Assert
        assert (has_pyc, same) == ((True, False), True)

    def test_wheel_file_install_of_the_same_wheel_has_the_index_fingerprint(self, prov):
        # Arrange
        index = prov["index"]
        # Act
        archive = prov["local_wheel"]
        # Assert
        assert archive["fingerprint"] == index["fingerprint"]

    def test_patched_rebuild_keeps_the_version(self, prov):
        # Arrange
        release = prov["index"]
        # Act
        patched = prov["patched_index"]
        # Assert
        assert (release["version"], patched["version"]) == ("1.0.0", "1.0.0")

    def test_patched_rebuild_looks_like_an_index_install(self, prov):
        # Arrange
        patched = prov["patched_index"]
        # Act
        source = patched["source"]
        # Assert
        assert source == "index"

    def test_patched_rebuild_of_the_same_version_has_another_fingerprint(self, prov):
        # Arrange
        release = prov["index"]
        # Act
        patched = prov["patched_index"]
        # Assert
        assert release["fingerprint"] != patched["fingerprint"]

    def test_fingerprint_ignores_installer_written_entries(self):
        # Arrange
        shipped = (
            "pkg/__init__.py,sha256=AAA,3\npkg-1.dist-info/METADATA,sha256=BBB,9\n"
        )
        installed = (
            shipped
            + "pkg/__pycache__/__init__.cpython-312.pyc,sha256=CCC,99\n"
            + "pkg-1.dist-info/INSTALLER,sha256=DDD,4\n"
            + "pkg-1.dist-info/direct_url.json,sha256=EEE,40\n"
            + "pkg-1.dist-info/RECORD,,\n"
            + "../../../bin/pkg,sha256=FFF,200\n"
        )
        # Act
        fingerprints = (record_fingerprint(installed), record_fingerprint(shipped))
        # Assert
        assert fingerprints[0] == fingerprints[1]

    def test_quoted_record_path_with_a_comma_is_parsed(self):
        # Arrange
        lines = ('"pkg/a,b.py",sha256=AAA,3\n', '"pkg/a,b.py",sha256=ZZZ,3\n')
        # Act
        fingerprints = {record_fingerprint(line) for line in lines}
        # Assert
        assert len(fingerprints) == 2


class TestEditedInPlace:
    def test_intact_install_lists_no_modified_file(self, prov):
        # Arrange
        info = prov["index"]
        # Act
        modified = info["modified_files"]
        # Assert
        assert modified == []

    def test_edited_installed_file_is_listed(self, prov, modified):
        # Arrange
        info = modified("edited")
        # Act
        modified = info["modified_files"]
        # Assert
        assert modified == [f"{NAME}/__init__.py"]

    def test_edit_in_place_keeps_the_fingerprint(self, prov, modified):
        # Arrange
        intact = prov["index"]
        # Act
        edited = modified("edited")
        # Assert
        assert edited["fingerprint"] == intact["fingerprint"]

    def test_edit_in_place_is_not_reproducible(self, prov, modified):
        # Arrange
        info = modified("edited")
        # Act
        reason = non_reproducible_reason(info) or ""
        # Assert
        assert "after installation" in reason

    def test_touched_but_unchanged_file_is_not_modified(self, prov, modified):
        # Arrange
        info = modified("touched")
        # Act
        modified = info["modified_files"]
        # Assert
        assert modified == []

    def test_deleted_installed_file_is_listed(self, prov, modified):
        # Arrange
        info = modified("deleted")
        # Act
        modified = info["modified_files"]
        # Assert
        assert modified == [f"{NAME}/__init__.py"]


@pytest.fixture
def on_sys_path():
    """Put a site on ``sys.path`` for one test (removed on teardown)."""
    added = []

    def add(site: Path) -> None:
        sys.path.insert(0, str(site))
        added.append(str(site))

    yield add
    for entry in added:
        sys.path.remove(entry)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(NAME, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRunMetadata:
    """The run record carries the provenance of what the run imported."""

    def test_run_metadata_records_package_provenance(
        self, sites, tmp_path, on_sys_path
    ):
        # Arrange
        on_sys_path(sites["local_dir"])
        module = _load(sites["local_dir"] / NAME / "__init__.py")
        (tmp_path / "proj").mkdir()
        # Act
        meta = collect_run_metadata(
            str(tmp_path / "proj" / "run.py"),
            modules={NAME: module},
            project_root=tmp_path / "proj",
        )
        # Assert
        assert meta["package_provenance"][NAME]["source"] == "local_dir"

    def test_close_keeps_start_provenance_of_a_distribution(
        self, sites, tmp_path, on_sys_path
    ):
        # Arrange
        on_sys_path(sites["index"])
        module = _load(sites["index"] / NAME / "__init__.py")
        start = {
            "project_root": str(tmp_path),
            "packages": {NAME: "1.0.0"},
            "package_provenance": {NAME: {"version": "1.0.0", "marker": "start"}},
        }
        # Act
        close = collect_close_metadata(start, modules={NAME: module})
        # Assert
        assert close["package_provenance"][NAME]["marker"] == "start"

    def test_close_examines_a_distribution_imported_after_start(
        self, sites, tmp_path, on_sys_path
    ):
        # Arrange
        on_sys_path(sites["index"])
        module = _load(sites["index"] / NAME / "__init__.py")
        # Act
        close = collect_close_metadata(
            {"project_root": str(tmp_path)}, modules={NAME: module}
        )
        # Assert
        assert close["package_provenance"][NAME]["source"] == "index"


def test_recording_stays_cheap_for_a_hundred_distributions():
    """Provenance of ~100 real installed distributions stays well under a second.

    One small ``RECORD`` read plus one ``stat`` per installed file: measured
    ~1-2 ms per distribution on a warm cache (an 18k-file distribution
    ~0.1 s). The bound is loose for slow CI runners.
    """
    # Arrange
    dists = [d for d in distributions() if d.read_text("RECORD")][:100]
    t0 = time.perf_counter()
    # Act
    for dist in dists:
        distribution_provenance(dist)
    elapsed = time.perf_counter() - t0
    # Assert
    assert (len(dists) > 10, elapsed < 0.03 * len(dists) + 0.5) == (True, True), (
        len(dists),
        elapsed,
    )


# EOF
