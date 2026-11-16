#!/usr/bin/env python3
# File: ./tests/integration/test_provenance_capture.py

"""End-to-end provenance capture of a real ``@session`` script.

A real script is run in a subprocess inside a throw-away project directory
holding ``config/PARAMS.yaml`` and an input CSV. No mocks:

* ``TestHookFailuresReachRunMetadata`` registers a genuinely raising start
  hook in the script and checks that the failure is recorded in the run's own
  ``CONFIGS/CONFIG.yaml`` and warned about — never silently swallowed.
* ``TestClewRecordsTheRun`` runs against a REAL clew store and checks that the
  config file is recorded as a run input and that the re-run metadata reaches
  the run record. It needs ``scitex_clew`` and ``SCITEX_STORE_DSN`` (the
  per-host PostgreSQL clew stores into); without them it is skipped with the
  reason stated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import scitex_io

SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    import scitex_io as sio
    import scitex_session

    if "--break-start-hook" in sys.argv:
        sys.argv.remove("--break-start-hook")

        def deliberately_broken_start_hook(session_id, script_path, metadata):
            raise RuntimeError("observer store unreachable")

        scitex_session.register_session_start_hook(deliberately_broken_start_hook)

    START_CALLS = []
    if "--record-start-hooks" in sys.argv:
        sys.argv.remove("--record-start-hooks")

        def recording_start_hook(session_id, script_path, metadata):
            START_CALLS.append([session_id, script_path, metadata is not None])

        scitex_session.register_session_start_hook(recording_start_hook)


    @scitex_session.session(verbose=False)
    def main(n: int = 3, CONFIG=scitex_session.INJECTED):
        if START_CALLS:
            print("START_CALLS=" + json.dumps(START_CALLS))
        df = sio.load("data.csv")
        df["above"] = df["y"] > CONFIG.PARAMS.THRESHOLD * n
        sio.save(df, "out.csv")
        return 0


    if __name__ == "__main__":
        main()
    """
)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "config" / "PARAMS.yaml").write_text("THRESHOLD: 0.5\n")
    (root / "data.csv").write_text("x,y\n1,2\n3,4\n")
    (root / "scripts" / "run.py").write_text(SCRIPT)
    return root


def _run_script(project: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/run.py", "--n", "2", *extra],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _finished_config(project: Path) -> dict:
    (cfg,) = (project / "scripts" / "run_out" / "FINISHED_SUCCESS").glob(
        "*/CONFIGS/CONFIG.yaml"
    )
    return dict(scitex_io.load(str(cfg)))


class TestStartHooksFireExactlyOnce:
    """One session = one start event, carrying the script path and metadata.

    The session manager used to re-fire the start hooks with no script path
    and no metadata, which made an observer re-open (overwrite) its run
    record without them.
    """

    def test_start_hook_fires_once_with_script_path_and_metadata(self, project):
        # Arrange
        script = str((project / "scripts" / "run.py").resolve())
        # Act
        result = _run_script(project, "--record-start-hooks")
        line = next(
            ln for ln in result.stdout.splitlines() if ln.startswith("START_CALLS=")
        )
        calls = json.loads(line.split("=", 1)[1])
        # Assert
        assert [(path, has_meta) for _, path, has_meta in calls] == [(script, True)]


class TestHookFailuresReachRunMetadata:
    """A raising observer hook is counted, warned and recorded."""

    def test_script_still_succeeds_when_an_observer_hook_raises(self, project):
        # Arrange
        # Act
        result = _run_script(project, "--break-start-hook")
        # Assert
        assert result.returncode == 0, result.stderr

    def test_failed_start_hook_is_recorded_in_run_config(self, project):
        # Arrange
        _run_script(project, "--break-start-hook")
        # Act
        failures = _finished_config(project)["_HOOK_FAILURES"]
        # Assert
        assert {
            "phase": "start",
            "hook": "deliberately_broken_start_hook",
            "error": "RuntimeError: observer store unreachable",
        } in failures

    def test_failed_start_hook_is_warned_about(self, project):
        # Arrange
        # Act
        result = _run_script(project, "--break-start-hook")
        # Assert
        assert "deliberately_broken_start_hook" in result.stdout + result.stderr

    def test_run_config_records_rerun_metadata(self, project):
        # Arrange
        _run_script(project)
        # Act
        prov = _finished_config(project)["_PROVENANCE"]
        # Assert
        assert (prov["args"], prov["seed"]) == ({"n": 2}, 42)


def _clew_available() -> str:
    try:
        import scitex_clew  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"scitex_clew not importable: {exc}"
    if not os.environ.get("SCITEX_STORE_DSN"):
        return "SCITEX_STORE_DSN not set (clew needs a real PostgreSQL store)"
    return ""


_CLEW_SKIP = _clew_available()

QUERY = textwrap.dedent(
    """
    import json, sys
    from scitex_clew._db import get_db

    db = get_db()
    script = sys.argv[1]
    runs = [r for r in db.list_runs(limit=100000) if r["script_path"] == script]
    run = runs[0]
    meta = run["metadata"]
    print(json.dumps({
        "status": run["status"],
        "metadata": json.loads(meta) if isinstance(meta, str) else meta,
        "inputs": sorted(db.get_file_hashes(run["session_id"], role="input")),
        "outputs": sorted(db.get_file_hashes(run["session_id"], role="output")),
    }))
    """
)


@pytest.fixture
def clew_run(project):
    result = _run_script(project)
    assert result.returncode == 0, result.stderr
    script = str((project / "scripts" / "run.py").resolve())
    query = subprocess.run(
        [sys.executable, "-c", QUERY, script],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert query.returncode == 0, query.stderr
    return project, json.loads(query.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(bool(_CLEW_SKIP), reason=_CLEW_SKIP or "clew available")
class TestClewRecordsTheRun:
    """Config files and re-run metadata reach the real clew run record."""

    def test_config_file_is_recorded_as_run_input(self, clew_run):
        # Arrange
        project, run = clew_run
        # Act
        params = str((project / "config" / "PARAMS.yaml").resolve())
        # Assert
        assert params in run["inputs"]

    def test_data_input_is_still_recorded(self, clew_run):
        # Arrange
        project, run = clew_run
        # Act
        data = str((project / "data.csv").resolve())
        # Assert
        assert data in run["inputs"]

    def test_run_metadata_carries_cli_args_and_seed(self, clew_run):
        # Arrange
        _, run = clew_run
        # Act
        meta = run["metadata"]
        # Assert
        assert (meta["args"], meta["seed"]) == ({"n": 2}, 42)

    def test_run_metadata_carries_session_output_base(self, clew_run):
        # Arrange
        project, run = clew_run
        # Act
        sdir_out = run["metadata"]["sdir_out"]
        # Assert
        assert Path(sdir_out).resolve() == (project / "scripts" / "run_out").resolve()

    def test_run_is_finalised_as_success(self, clew_run):
        # Arrange
        _, run = clew_run
        # Act
        status = run["status"]
        # Assert
        assert status == "success"


# EOF
