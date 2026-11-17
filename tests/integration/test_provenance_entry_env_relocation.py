#!/usr/bin/env python3
# File: ./tests/integration/test_provenance_entry_env_relocation.py

"""Provenance a re-run depends on: environment, entry point, final paths.

Real sessions in real subprocesses, in a throw-away project; observers are
real hook functions registered by the session's own code and printing what
they receive. No mocks.

* scitex-session#64 — the third-party distributions a run imports (also
  lazily, inside ``main``) are recorded with their version, including
  editable installs and distributions without ``top_level.txt``; imported
  code no distribution owns is listed, not dropped.
* scitex-session#65 — a session started by ``python -c`` is not attributed to
  any file (in particular not to the session library's own modules); the
  command text identifies it instead.
* scitex-session#66 — files saved inside the run directory are reported at
  their FINAL location (``FINISHED_*``) to close observers, for success,
  error and other exit statuses.
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

import scitex_session

#: Run the in-tree package in the subprocess, not whatever is installed.
_SRC = str(Path(scitex_session.__file__).resolve().parent.parent)

OBSERVERS = textwrap.dedent(
    """
    import json
    import scitex_io as sio
    import scitex_session

    SAVED = []

    def record_save(path, *args, **kwargs):
        SAVED.append(str(path))

    sio.register_post_save_hook(record_save)

    def on_start(session_id, script_path, metadata):
        print("START=" + json.dumps(
            {"script_path": script_path, "metadata": metadata}))

    def on_close(status, exit_code, metadata=None):
        print("CLOSE=" + json.dumps(
            {"status": status, "metadata": metadata, "saved": SAVED}))

    scitex_session.register_session_start_hook(on_start)
    scitex_session.register_session_close_hook(on_close)
    """
)

SCRIPT = OBSERVERS + textwrap.dedent(
    """

    @scitex_session.session(verbose=False)
    def main(outcome: str = "ok", CONFIG=scitex_session.INJECTED):
        import provtest_mod            # lazy: imported only inside the run
        import provtest_record_only
        import provtest_editable
        import provtest_nodist
        sio.save({"v": provtest_mod.VALUE}, str(CONFIG["SDIR_RUN"]) + "/result.json")
        if outcome == "raise":
            raise RuntimeError("deliberate failure")
        return 0 if outcome == "ok" else 2


    if __name__ == "__main__":
        main()
    """
)


def _write_dist(site: Path, name: str, version: str, files: dict, meta: dict):
    """Install a real (wheel-layout) distribution into ``site``."""
    for rel, text in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text(text)
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    )
    record = [f"{rel},," for rel in files] + [f"{info.name}/METADATA,,"]
    (info / "RECORD").write_text("\n".join(record) + "\n")
    for fname, text in meta.items():
        (info / fname).write_text(text)


@pytest.fixture
def env(tmp_path):
    """A project, plus a site dir holding the third-party code it imports."""
    project = tmp_path / "proj"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "run.py").write_text(SCRIPT)
    site = tmp_path / "site-packages"
    site.mkdir()
    # 1. ordinary distribution with top_level.txt
    _write_dist(
        site,
        "provtest-dist",
        "1.2.3",
        {"provtest_mod/__init__.py": "VALUE = 7\n"},
        {"top_level.txt": "provtest_mod\n"},
    )
    # 2. distribution without top_level.txt (RECORD only)
    _write_dist(
        site,
        "provtest-record-only",
        "0.4.0",
        {"provtest_record_only.py": "X = 1\n"},
        {},
    )
    # 3. editable install: RECORD lists only a .pth; source tree elsewhere
    src = tmp_path / "editable-src"
    (src / "provtest_editable").mkdir(parents=True)
    (src / "provtest_editable" / "__init__.py").write_text("Y = 2\n")
    _write_dist(
        site,
        "provtest-editable",
        "9.9.9",
        {"_provtest_editable.pth": str(src) + "\n"},
        {
            "direct_url.json": json.dumps(
                {"url": src.as_uri(), "dir_info": {"editable": True}}
            )
        },
    )
    # 4. importable code that no distribution owns
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "provtest_nodist.py").write_text("Z = 3\n")
    # keep the caller's PYTHONPATH (after ours): the environment under test
    pythonpath = os.pathsep.join(
        p
        for p in (_SRC, str(site), str(src), str(loose), os.environ.get("PYTHONPATH"))
        if p
    )
    return {"project": project, "pythonpath": pythonpath, "loose": loose}


def _run(env, argv, cwd):
    proc_env = dict(os.environ, PYTHONPATH=env["pythonpath"])
    proc_env.pop("SCITEX_SESSION_OUT_DIR", None)
    return subprocess.run(
        [sys.executable, *argv],
        cwd=cwd,
        env=proc_env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _payload(result, tag):
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith(tag + "=")]
    assert lines, (tag, result.returncode, result.stdout[-3000:], result.stderr[-3000:])
    return json.loads(lines[-1].split("=", 1)[1])


@pytest.fixture
def ok_run(env):
    result = _run(env, ["scripts/run.py"], env["project"])
    assert result.returncode == 0, result.stderr[-3000:]
    return env, result


class TestThirdPartyDistributionsAreRecorded:
    """#64: what a fresh environment needs to re-run the script."""

    def test_lazily_imported_distribution_is_recorded_with_version(self, ok_run):
        # Arrange
        _, result = ok_run
        # Act
        packages = _payload(result, "CLOSE")["metadata"]["packages"]
        # Assert
        assert packages.get("provtest-dist") == "1.2.3"

    def test_distribution_without_top_level_txt_is_recorded(self, ok_run):
        # Arrange
        _, result = ok_run
        # Act
        packages = _payload(result, "CLOSE")["metadata"]["packages"]
        # Assert
        assert packages.get("provtest-record-only") == "0.4.0"

    def test_editable_install_is_recorded(self, ok_run):
        # Arrange
        _, result = ok_run
        # Act
        packages = _payload(result, "CLOSE")["metadata"]["packages"]
        # Assert
        assert packages.get("provtest-editable") == "9.9.9"

    def test_code_no_distribution_owns_is_listed_as_unresolved(self, ok_run):
        # Arrange
        env, result = ok_run
        # Act
        unresolved = _payload(result, "CLOSE")["metadata"]["unresolved_imports"]
        # Assert
        assert unresolved.get("provtest_nodist") == str(
            (env["loose"] / "provtest_nodist.py").resolve()
        )

    def test_stdlib_is_neither_a_package_nor_unresolved(self, ok_run):
        # Arrange
        _, result = ok_run
        # Act
        meta = _payload(result, "CLOSE")["metadata"]
        # Assert
        assert (
            "json" not in meta["unresolved_imports"] and "json" not in meta["packages"]
        )

    def test_run_config_carries_the_close_time_packages(self, ok_run):
        # Arrange
        env, _ = ok_run
        (cfg,) = (env["project"] / "scripts" / "run_out" / "FINISHED_SUCCESS").glob(
            "*/CONFIGS/CONFIG.yaml"
        )
        # Act
        packages = dict(scitex_io.load(str(cfg)))["_PROVENANCE"]["packages"]
        # Assert
        assert packages.get("provtest-dist") == "1.2.3"


COMMAND = OBSERVERS + textwrap.dedent(
    """
    @scitex_session.session(verbose=False)
    def main(CONFIG=scitex_session.INJECTED):
        sio.save({"ok": True}, str(CONFIG["SDIR_RUN"]) + "/c.json")
        return 0

    main()
    """
)


@pytest.fixture
def command_run(env, tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    result = _run(env, ["-c", COMMAND], workdir)
    assert result.returncode == 0, result.stderr[-3000:]
    return workdir, result


class TestCommandEntryIsNotAttributedToAFile:
    """#65: ``python -c`` has no script; never blame a library file."""

    def test_start_hook_receives_no_script_path(self, command_run):
        # Arrange
        _, result = command_run
        # Act
        script_path = _payload(result, "START")["script_path"]
        # Assert
        assert script_path is None

    def test_entry_is_recorded_as_a_command(self, command_run):
        # Arrange
        _, result = command_run
        # Act
        entry = _payload(result, "START")["metadata"]["entry"]
        # Assert
        assert entry["kind"] == "command"

    def test_command_text_and_hash_identify_the_run(self, command_run):
        # Arrange
        import hashlib

        _, result = command_run
        # Act
        entry = _payload(result, "START")["metadata"]["entry"]
        # Assert
        assert (entry["command"], entry["command_sha256"]) == (
            COMMAND,
            hashlib.sha256(COMMAND.encode()).hexdigest(),
        )

    def test_no_session_library_module_is_recorded_as_project_code(self, command_run):
        # Arrange
        _, result = command_run
        # Act
        local = _payload(result, "START")["metadata"]["local_modules"]
        # Assert
        assert [p for p in local if "scitex_session" in p] == []

    def test_project_root_is_the_working_directory(self, command_run):
        # Arrange
        workdir, result = command_run
        # Act
        root = _payload(result, "START")["metadata"]["project_root"]
        # Assert
        assert Path(root) == workdir.resolve()

    def test_outputs_land_under_the_working_directory(self, command_run):
        # Arrange
        workdir, _ = command_run
        # Act
        found = list(workdir.glob("python_c_out/FINISHED_SUCCESS/*/c.json"))
        # Assert
        assert len(found) == 1


def _relocated(path: str, relocations: list) -> str:
    """Apply the close metadata's relocations the way an observer must."""
    real = os.path.realpath(path)
    for move in relocations:
        if real == move["from"] or real.startswith(move["from"] + os.sep):
            return move["to"] + real[len(move["from"]) :]
    return real


@pytest.mark.parametrize(
    "outcome, status_dir, exit_code",
    [
        ("ok", "FINISHED_SUCCESS", 0),
        ("raise", "FINISHED_ERROR", 1),
        ("two", "FINISHED", None),
    ],
)
class TestRunDirOutputsAreReportedAtTheirFinalPath:
    """#66: the RUNNING/<ID> path does not survive close; the final one does."""

    def _close(self, env, outcome):
        result = _run(env, ["scripts/run.py", "--outcome", outcome], env["project"])
        assert result.returncode == exit_code_of(outcome), result.stderr[-3000:]
        return _payload(result, "CLOSE")

    def test_saved_file_was_recorded_under_running(
        self, env, outcome, status_dir, exit_code
    ):
        # Arrange
        close = self._close(env, outcome)
        # Act
        (saved,) = [p for p in close["saved"] if p.endswith("result.json")]
        # Assert
        assert "/RUNNING/" in saved and not os.path.exists(saved)

    def test_relocation_maps_the_recorded_path_to_an_existing_file(
        self, env, outcome, status_dir, exit_code
    ):
        # Arrange
        close = self._close(env, outcome)
        (saved,) = [p for p in close["saved"] if p.endswith("result.json")]
        # Act
        final = _relocated(saved, close["metadata"]["relocations"])
        # Assert
        assert os.path.isfile(final) and f"/{status_dir}/" in final

    def test_sdir_run_final_is_the_status_directory(
        self, env, outcome, status_dir, exit_code
    ):
        # Arrange
        close = self._close(env, outcome)
        # Act
        final_dir = Path(close["metadata"]["sdir_run_final"])
        # Assert
        assert (final_dir.parent.name, final_dir.is_dir()) == (status_dir, True)


def exit_code_of(outcome: str) -> int:
    # ``raise`` propagates out of main(); ``two`` is a return value, which the
    # decorator maps to the session status but not to the process exit code.
    return 1 if outcome == "raise" else 0


# EOF
