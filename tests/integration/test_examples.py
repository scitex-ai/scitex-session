"""Smoke test: every example script under examples/ runs to completion.

Each example runs the way its docstring tells a user to run it: from a copy of
``examples/`` (so ``./config/*.yaml`` is found — examples read their
parameters from config since 0.6.1, and the run's ``*_out/`` lands in
``tmp_path``, never in the source tree), as a child process with an EXPLICIT
environment. The parent environment is never passed through: only the keys
named in :func:`_child_env` reach the child.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: The repository's ``examples/`` — tests/integration/ -> tests/ -> repo root.
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.py"))

#: A Postgres endpoint that refuses connections immediately (loopback port 9:
#: nothing listens). Used as the store when the test environment names none,
#: so an installed provenance observer (scitex-clew) can never fall through
#: to a default — shared — store from a test run.
_REFUSING_STORE_DSN = "postgresql://nobody@127.0.0.1:9/none?connect_timeout=2"

#: Extra argv per example. An example absent from this table runs bare, as
#: its docstring says (``python quickstart.py``).
EXAMPLE_ARGS = {
    "measure_run_record_size.py": lambda tmp: [
        "--out-dir",
        str(tmp / "measure"),
        "--sessions",
        "1",
    ],
}


def _child_env(tmp_path: Path) -> dict:
    """The child's whole environment, by allowlist.

    ``HOME`` is the test's own directory so per-user state (``~/.scitex``)
    stays out of the developer's home. ``SCITEX_STORE_DSN`` is forwarded
    only when the test run names one (a disposable store); otherwise the
    refusing endpoint above is used.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TMPDIR": str(tmp_path),
        "SCITEX_STORE_DSN": os.environ.get("SCITEX_STORE_DSN") or _REFUSING_STORE_DSN,
    }
    for key in ("COVERAGE_PROCESS_START", "COVERAGE_FILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def test_examples_directory_contains_at_least_one_script():
    # Arrange
    # Act
    count = len(EXAMPLES)
    # Assert
    assert count > 0, f"No example scripts found under {EXAMPLES_DIR}"


def test_every_example_with_args_exists():
    # Arrange
    names = {e.name for e in EXAMPLES}
    # Act
    stale = set(EXAMPLE_ARGS) - names
    # Assert
    assert not stale, f"EXAMPLE_ARGS names examples that do not exist: {stale}"


@pytest.mark.parametrize(
    "example",
    EXAMPLES,
    ids=[e.name for e in EXAMPLES] if EXAMPLES else ["<none>"],
)
def test_example_script_exits_zero_when_run(example, tmp_path):
    # Arrange — a copy of examples/ (script + config/) to run from.
    workdir = tmp_path / "examples"
    shutil.copytree(EXAMPLES_DIR, workdir)
    argv = EXAMPLE_ARGS.get(example.name, lambda _tmp: [])(tmp_path)
    # Act
    r = subprocess.run(
        [sys.executable, example.name, *argv],
        cwd=workdir,
        env=_child_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    # Assert
    assert r.returncode == 0, (
        f"{example.name} failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
