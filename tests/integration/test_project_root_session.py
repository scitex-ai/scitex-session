#!/usr/bin/env python3
# File: ./tests/integration/test_project_root_session.py

"""scitex-session#71 end to end: a real ``@session`` run in a non-git project.

A throw-away research project ``proj/{config,data,scripts}`` that is NOT a
git repository, its script run in a real subprocess from inside
``scripts/`` (the case where neither git nor the working directory names
the project). A real session-start observer prints the metadata it gets —
what scitex-clew uses as its capture boundary. ``HOME`` is the test's
``tmp_path`` so the upward search is bounded to the test tree. No mocks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import scitex_session

_SRC = str(Path(scitex_session.__file__).resolve().parent.parent)

SCRIPT = textwrap.dedent(
    """
    import json
    import scitex_session

    def on_start(session_id, script_path, metadata):
        print("START=" + json.dumps(metadata))

    scitex_session.register_session_start_hook(on_start)

    @scitex_session.session(verbose=False{extra})
    def main(CONFIG=scitex_session.INJECTED):
        return 0

    if __name__ == "__main__":
        main()
    """
)


def _project(tmp_path: Path, extra: str = "") -> Path:
    project = tmp_path / "proj"
    for sub in ("config", "data", "scripts"):
        (project / sub).mkdir(parents=True)
    (project / "data" / "x.csv").write_text("a\n1\n")
    (project / "scripts" / "run.py").write_text(SCRIPT.replace("{extra}", extra))
    return project


def _start_metadata(tmp_path: Path, project: Path, **env_extra: str) -> dict:
    env = dict(os.environ, PYTHONPATH=_SRC, HOME=str(tmp_path), **env_extra)
    env.pop("SCITEX_SESSION_OUT_DIR", None)
    if "SCITEX_SESSION_PROJECT_ROOT" not in env_extra:
        env.pop("SCITEX_SESSION_PROJECT_ROOT", None)
    result = subprocess.run(
        [sys.executable, "run.py"],
        cwd=project / "scripts",
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("START=")]
    assert lines, (result.returncode, result.stdout[-3000:], result.stderr[-3000:])
    return json.loads(lines[-1].split("=", 1)[1])


@pytest.fixture
def home(tmp_path):
    return tmp_path.resolve()


class TestNonGitProjectSession:
    def test_project_root_is_the_project_dir(self, home):
        # Arrange
        project = _project(home)
        # Act
        meta = _start_metadata(home, project)
        # Assert
        assert meta["project_root"] == str(project)

    def test_detection_rule_is_recorded(self, home):
        # Arrange
        project = _project(home)
        # Act
        meta = _start_metadata(home, project)
        # Assert
        assert (meta["project_root_source"], meta["project_root_marker"]) == (
            "marker",
            "scripts/ + config/",
        )

    def test_env_var_overrides_detection(self, home):
        # Arrange
        project = _project(home)
        # Act
        meta = _start_metadata(
            home, project, SCITEX_SESSION_PROJECT_ROOT=str(project / "data")
        )
        # Assert
        assert (meta["project_root"], meta["project_root_source"]) == (
            str(project / "data"),
            "env",
        )

    def test_decorator_argument_overrides_env_var(self, home):
        # Arrange
        project = _project(home, extra=", project_root=__import__('os').getcwd()")
        # Act
        meta = _start_metadata(
            home, project, SCITEX_SESSION_PROJECT_ROOT=str(project / "data")
        )
        # Assert
        assert (meta["project_root"], meta["project_root_source"]) == (
            str(project / "scripts"),
            "argument",
        )


# EOF
