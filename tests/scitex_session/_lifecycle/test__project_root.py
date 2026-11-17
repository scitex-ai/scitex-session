#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__project_root.py
"""Project-root detection (scitex-session#71).

Real directory trees, real ``git`` repositories, real environment variables
— no mocks. ``HOME`` is pointed at each test's ``tmp_path`` so the upward
search's ``$HOME`` ceiling bounds it to the test's own tree, whatever lies
above it on the machine running the suite.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterator, Optional

import pytest

from scitex_session._lifecycle._project_root import (
    PROJECT_ROOT_ENV,
    resolve_project_root,
)
from scitex_session._lifecycle._provenance import collect_run_metadata

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git executable not on PATH")


@contextlib.contextmanager
def _environment(home: Path, cwd: Path, explicit: Optional[str] = None) -> Iterator:
    """Real process state: ``$HOME``, ``$SCITEX_SESSION_PROJECT_ROOT``, cwd."""
    saved = {k: os.environ.get(k) for k in ("HOME", PROJECT_ROOT_ENV)}
    saved_cwd = os.getcwd()
    os.environ["HOME"] = str(home)
    if explicit is None:
        os.environ.pop(PROJECT_ROOT_ENV, None)
    else:
        os.environ[PROJECT_ROOT_ENV] = explicit
    os.chdir(cwd)
    try:
        yield
    finally:
        os.chdir(saved_cwd)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture
def home(tmp_path):
    """``tmp_path`` as ``$HOME`` and cwd; no explicit root in the environment."""
    root = tmp_path.resolve()
    with _environment(root, root):
        yield root


def _script(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('hi')\n")
    return path.resolve()


def _research_project(root: Path) -> Path:
    """The SciTeX research layout without any VCS: config/ data/ scripts/."""
    for sub in ("config", "data", "scripts"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "config" / "PATH.yaml").write_text("DATA: ./data\n")
    (root / "data" / "x.csv").write_text("a\n1\n")
    return _script(root / "scripts" / "analysis" / "run.py")


def _git_init(repo: Path) -> None:
    subprocess.run([GIT, "-C", str(repo), "init", "-q"], check=True)


class TestNonGitProject:
    def test_research_layout_root_is_the_project_dir_not_scripts(self, home):
        # Arrange
        script = _research_project(home / "proj")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["project_root"] == str(home / "proj")

    def test_rule_and_marker_are_recorded(self, home):
        # Arrange
        script = _research_project(home / "proj")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert (meta["project_root_source"], meta["project_root_marker"]) == (
            "marker",
            "scripts/ + config/",
        )

    def test_pyproject_marks_the_root(self, home):
        # Arrange
        (home / "proj").mkdir()
        (home / "proj" / "pyproject.toml").write_text("[project]\nname='p'\n")
        script = _script(home / "proj" / "src" / "deep" / "run.py")
        # Act
        result = resolve_project_root(script)
        # Assert
        assert result["project_root"] == str(home / "proj")

    def test_config_alone_beside_the_script_is_not_a_root_marker(self, home):
        # Arrange: config/ beside the script is the cwd-config pattern, not a
        # project layout; the outer pyproject.toml is the real root.
        (home / "proj").mkdir()
        (home / "proj" / "pyproject.toml").write_text("")
        script = _script(home / "proj" / "scripts" / "run.py")
        (script.parent / "config").mkdir()
        # Act
        result = resolve_project_root(script)
        # Assert
        assert result["project_root"] == str(home / "proj")

    def test_scitex_runtime_dir_is_not_a_marker(self, home):
        # Arrange: a tool that ran from scripts/ left .scitex/ there
        script = _research_project(home / "proj")
        (script.parent / ".scitex" / "clew").mkdir(parents=True)
        # Act
        result = resolve_project_root(script)
        # Assert
        assert result["project_root"] == str(home / "proj")

    def test_project_data_is_inside_the_root(self, home):
        # Arrange
        script = _research_project(home / "proj")
        # Act
        root = Path(collect_run_metadata(str(script))["project_root"])
        # Assert
        assert (home / "proj" / "data" / "x.csv").relative_to(root)


class TestNestedProjects:
    def test_nearest_marked_ancestor_wins(self, home):
        # Arrange: an inner package project inside an outer research project
        _research_project(home / "outer")
        inner = home / "outer" / "vendor" / "inner"
        inner.mkdir(parents=True)
        (inner / "setup.cfg").write_text("[metadata]\nname = inner\n")
        script = _script(inner / "scripts" / "run.py")
        # Act
        result = resolve_project_root(script)
        # Assert
        assert result["project_root"] == str(inner)


@needs_git
class TestGitProject:
    def test_git_root_is_unchanged(self, home):
        # Arrange
        repo = home / "repo"
        script = _research_project(repo)
        _git_init(repo)
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert (meta["project_root"], meta["project_root_source"]) == (
            str(repo),
            "git",
        )

    def test_git_root_wins_over_a_marker_nested_inside_the_repo(self, home):
        # Arrange: a sub-package with its own pyproject.toml inside the repo
        repo = home / "repo"
        repo.mkdir()
        _git_init(repo)
        (repo / "pkg").mkdir()
        (repo / "pkg" / "pyproject.toml").write_text("")
        script = _script(repo / "pkg" / "scripts" / "run.py")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert meta["project_root"] == str(repo)

    def test_git_repository_at_home_is_not_the_project(self, home):
        # Arrange: a dotfiles repository at $HOME holding a non-git project
        _git_init(home)
        script = _research_project(home / "proj")
        # Act
        meta = collect_run_metadata(str(script))
        # Assert
        assert (meta["project_root"], meta["project_root_source"]) == (
            str(home / "proj"),
            "marker",
        )


class TestExplicitOverride:
    def test_argument_wins_over_markers_and_env(self, home):
        # Arrange
        script = _research_project(home / "proj")
        # Act
        with _environment(home, home, explicit=str(home / "proj")):
            meta = collect_run_metadata(str(script), project_root=str(script.parent))
        # Assert
        assert (meta["project_root"], meta["project_root_source"]) == (
            str(script.parent),
            "argument",
        )

    @needs_git
    def test_env_var_wins_over_git_root(self, home):
        # Arrange
        repo = home / "repo"
        script = _research_project(repo)
        _git_init(repo)
        # Act
        with _environment(home, home, explicit=str(repo / "scripts")):
            meta = collect_run_metadata(str(script))
        # Assert
        assert (meta["project_root"], meta["project_root_source"]) == (
            str(repo / "scripts"),
            "env",
        )

    def test_missing_explicit_dir_fails_loudly(self, home):
        # Arrange
        script = _research_project(home / "proj")
        environ = {"HOME": str(home), PROJECT_ROOT_ENV: str(home / "typo")}
        # Act
        with pytest.raises(ValueError, match="not an existing directory") as err:
            resolve_project_root(script, environ=environ)
        # Assert
        assert "typo" in str(err.value)


class TestFallbacks:
    def test_cwd_containing_the_script_is_the_root(self, home):
        # Arrange: no marker anywhere; run from the project directory
        script = _script(home / "work" / "code" / "run.py")
        # Act
        result = resolve_project_root(script, cwd=home / "work")
        # Assert
        assert (result["project_root"], result["project_root_source"]) == (
            str(home / "work"),
            "cwd",
        )

    def test_loose_script_falls_back_to_its_dir(self, home):
        # Arrange: no marker, cwd ($HOME) is never a root
        script = _script(home / "loose" / "s.py")
        # Act
        result = resolve_project_root(script)
        # Assert
        assert (result["project_root"], result["project_root_source"]) == (
            str(script.parent),
            "script_dir",
        )

    def test_loose_script_fallback_is_warned(self, home, caplog):
        # Arrange
        script = _script(home / "loose" / "s.py")
        # Act
        with caplog.at_level(logging.WARNING):
            resolve_project_root(script)
        # Assert
        assert "no project root found" in caplog.text

    def test_search_never_reaches_home(self, home):
        # Arrange: markers AT $HOME (~/pyproject.toml, ~/config + ~/scripts)
        (home / "pyproject.toml").write_text("")
        (home / "config").mkdir()
        (home / "scripts").mkdir()
        script = _script(home / "loose" / "s.py")
        # Act
        result = resolve_project_root(script)
        # Assert
        assert result["project_root"] == str(script.parent)

    def test_no_script_uses_the_marked_ancestor_of_cwd(self, home):
        # Arrange: python -c / REPL from inside scripts/
        script = _research_project(home / "proj")
        # Act
        result = resolve_project_root(None, cwd=script.parent)
        # Assert
        assert result["project_root"] == str(home / "proj")

    def test_no_script_and_no_marker_uses_cwd_without_warning(self, home, caplog):
        # Arrange
        (home / "w").mkdir()
        # Act
        with caplog.at_level(logging.WARNING):
            result = resolve_project_root(None, cwd=home / "w")
        # Assert
        assert (result["project_root"], caplog.text) == (str(home / "w"), "")


class TestSessionArgument:
    def test_session_start_accepts_project_root(self):
        # Arrange
        import inspect

        from scitex_session._lifecycle._start import start

        # Act
        params = inspect.signature(start).parameters
        # Assert
        assert "project_root" in params


# EOF
