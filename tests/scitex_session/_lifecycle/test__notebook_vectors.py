#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__notebook_vectors.py
"""Shared notebook-path conformance vectors (scitex-io#197).

``tests/scitex_session/conformance/notebook-path-vectors.json`` and
``run_notebook_vectors.py`` are byte-identical in scitex-io, whose suite runs
the same vectors against ``scitex_io._notebook.resolve_notebook`` (the rule
``scitex_io.save`` uses to route a notebook's relative saves to
``<notebook>_out/``). Here they run against
``scitex_session._lifecycle._notebook.resolve_notebook``. A change to the rule
must update both implementations and both files in both repositories.

Real IPython shell, real launcher processes (``/proc/<pid>/cmdline``), real
notebooks on disk -- no mocks. The runner executes in a fresh interpreter so
its IPython singleton never leaks into the rest of the suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONFORMANCE = Path(__file__).resolve().parents[1] / "conformance"
VECTORS = CONFORMANCE / "notebook-path-vectors.json"
RUNNER = CONFORMANCE / "run_notebook_vectors.py"
CASES = json.loads(VECTORS.read_text())["cases"]
SRC = Path(__file__).resolve().parents[3] / "src"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the launcher's command line is read from /proc",
)


@pytest.fixture(scope="module")
def vector_results(tmp_path_factory):
    pytest.importorskip("IPython")
    parent = tmp_path_factory.mktemp("nbvec")
    env = dict(os.environ)
    for key in ("SCITEX_NOTEBOOK_PATH", "JPY_SESSION_NAME", "JPY_PARENT_PID"):
        env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(SRC), env.get("PYTHONPATH", "")) if p
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            str(VECTORS),
            "scitex_session._lifecycle._notebook:resolve_notebook",
            str(parent),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(parent),
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"vector runner failed:\n{proc.stderr[-4000:]}")
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    return {r["id"]: r for r in results}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_notebook_path_vector_resolves_as_specified(case, vector_results):
    # Arrange
    expect = {"path": case["expect"]["path"], "source": case["expect"].get("source")}
    # Act
    got = vector_results[case["id"]]
    # Assert
    assert {"path": got["path"], "source": got["source"]} == expect
