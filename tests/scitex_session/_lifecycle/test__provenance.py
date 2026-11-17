#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__provenance.py

"""Tests for the re-run metadata the session hands to provenance observers.

Real files, a real ``git`` repository and real imported modules — no mocks.
Each test asserts one property of :func:`collect_run_metadata`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_session._lifecycle._provenance import collect_run_metadata

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git executable not on PATH")


def _import_from_path(path: Path):
    """Import a real module from ``path`` without touching ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def git_project(tmp_path):
    """A committed git project holding a script and a local helper module."""
    repo = tmp_path / "proj"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "run.py").write_text("print('hi')\n")
    (repo / "scripts" / "helper_mod_for_provenance.py").write_text("VALUE = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(
        repo,
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "init",
    )
    return repo


class TestRerunInputs:
    """CLI args, argv, seed and cwd — what a re-run must replay."""

    def test_seed_is_recorded(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script), seed=1234)
        # Assert
        assert meta["seed"] == 1234

    def test_parsed_cli_args_are_recorded_as_a_mapping(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        args = argparse.Namespace(n=3, mode="fast")
        # Act
        meta = collect_run_metadata(str(script), args=args)
        # Assert
        assert meta["args"] == {"n": 3, "mode": "fast"}

    def test_argv_is_recorded_verbatim(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["argv"] == list(sys.argv)

    def test_cwd_is_recorded_because_relative_paths_resolve_against_it(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["cwd"] == os.getcwd()

    def test_non_json_arg_values_are_stringified_not_dropped(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        args = argparse.Namespace(path=Path("/data/x.csv"))
        # Act
        meta = collect_run_metadata(str(script), args=args)
        # Assert
        assert meta["args"] == {"path": "/data/x.csv"}

    def test_whole_metadata_is_json_serialisable(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        args = argparse.Namespace(path=Path("/x"), obj=object())
        # Act
        meta = collect_run_metadata(str(script), args=args, seed=1)
        # Assert
        assert json.loads(json.dumps(meta)) == meta


class TestSessionDirectories:
    """The run's output locations, for clew's unrecorded-output check."""

    def test_sdir_run_and_sdir_out_are_recorded_as_strings(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        run_dir = tmp_path / "s_out" / "RUNNING" / "ID"
        # Act
        meta = collect_run_metadata(
            str(script), sdir_run=str(run_dir), sdir_out=str(tmp_path / "s_out")
        )
        # Assert
        assert (meta["sdir_run"], meta["sdir_out"]) == (
            str(run_dir),
            str(tmp_path / "s_out"),
        )

    def test_notebook_path_is_recorded_for_ipynb_scripts(self, tmp_path):
        # Arrange
        nb = tmp_path / "analysis.ipynb"
        nb.write_text("{}")
        # Act
        meta = collect_run_metadata(str(nb))
        # Assert
        assert meta["notebook_path"] == str(nb)


@needs_git
class TestGitState:
    """Commit + dirty state of the repository that holds the script."""

    def test_commit_matches_repository_head(self, git_project):
        # Arrange
        script = git_project / "scripts" / "run.py"
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["git"]["commit"] == _git(git_project, "rev-parse", "HEAD")

    def test_clean_tree_is_reported_not_dirty(self, git_project):
        # Arrange
        script = git_project / "scripts" / "run.py"
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["git"]["dirty"] is False

    def test_modified_tracked_file_is_reported_dirty(self, git_project):
        # Arrange
        script = git_project / "scripts" / "run.py"
        script.write_text("print('changed')\n")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["git"]["dirty_paths"] == ["scripts/run.py"]

    def test_script_outside_any_repository_is_reported_as_such(self, tmp_path):
        # Arrange
        script = tmp_path / "loose" / "s.py"
        script.parent.mkdir()
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["git"] == {"repository": False}


@needs_git
class TestProjectRoot:
    """The project boundary observers use for "project-local" code and files."""

    def test_project_root_is_the_git_root_for_a_script_in_a_repository(
        self, git_project
    ):
        # Arrange
        script = git_project / "scripts" / "run.py"
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["project_root"] == str(git_project.resolve())

    def test_project_root_is_the_script_dir_outside_any_repository(self, tmp_path):
        # Arrange
        script = tmp_path / "loose" / "s.py"
        script.parent.mkdir()
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["project_root"] == str(script.parent.resolve())


@needs_git
class TestLocalModuleHashes:
    """Imported project-local modules are code inputs; their hashes are recorded."""

    def test_imported_local_module_hash_is_recorded(self, git_project):
        # Arrange
        script = git_project / "scripts" / "run.py"
        helper = git_project / "scripts" / "helper_mod_for_provenance.py"
        modules = {"helper_mod_for_provenance": _import_from_path(helper)}
        # Act
        meta = collect_run_metadata(str(script), modules=modules)
        # Assert
        assert (
            meta["local_modules"][str(helper.resolve())]
            == hashlib.sha256(helper.read_bytes()).hexdigest()
        )

    def test_installed_packages_are_not_treated_as_local(self, git_project):
        # Arrange
        script = git_project / "scripts" / "run.py"
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert not any("site-packages" in p for p in meta["local_modules"])


class TestPackageVersions:
    """Versions of the distributions this process imported."""

    def test_imported_distribution_version_is_recorded(self, tmp_path):
        # Arrange
        from importlib.metadata import version

        script = tmp_path / "s.py"
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["packages"]["pytest"] == version("pytest")

    def test_python_version_is_recorded(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["python"]["version"] == sys.version.split()[0]


class TestCaptureErrorsAreRecorded:
    """A collector that cannot run is recorded, not silently skipped."""

    def test_clean_capture_has_no_errors(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["capture_errors"] == {}

    def test_missing_git_executable_is_recorded_as_capture_error(self, tmp_path):
        # Arrange
        script = tmp_path / "s.py"
        script.write_text("")
        absent_git = str(tmp_path / "no-such-bin" / "git")
        # Act
        meta = collect_run_metadata(str(script), git_executable=absent_git)
        # Assert
        assert "git" in meta["capture_errors"]


# EOF
