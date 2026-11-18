#!/usr/bin/env python3
# File: ./examples/measure_run_record_size.py
"""Measure what a session run leaves on disk: run directories + environment store.

Runs ``--sessions`` real ``@session`` runs of a small load/save script in a
fresh project under ``--out-dir`` with the interpreter ``--python`` (so two
installed scitex-session versions can be compared), then prints, as JSON, the
size of each run directory, of its ``CONFIGS/CONFIG.yaml`` / ``CONFIG.pkl``,
and of the project's environment store (``.scitex/session/environments``).

The child processes get an explicit environment (HOME, PATH, LANG, TMPDIR and
``SCITEX_STORE_DSN``) — never this process's whole environment. A provenance
observer (scitex-clew) that is installed records into ``SCITEX_STORE_DSN``, so
it must be set; the script refuses to run without it.

    SCITEX_STORE_DSN=... python examples/measure_run_record_size.py \\
        --python /path/to/venv/bin/python --out-dir /scratch/me/measure --sessions 3
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROBE = """\
import scitex_io as sio
import scitex_session as stx_session


@stx_session.session(verbose=False)
def main(CONFIG=stx_session.INJECTED):
    df = sio.load("data/a.csv")
    sio.save(df, "b.csv")
    return 0


if __name__ == "__main__":
    main()
"""


def _tree_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _make_project(root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "pyproject.toml").write_text("")
    (root / "data" / "a.csv").write_text("x\n1\n2\n")
    script = root / "scripts" / "probe.py"
    script.write_text(PROBE)
    return script


def measure(python: str, out_dir: Path, sessions: int, dsn: str) -> dict:
    project = out_dir / "project"
    script = _make_project(project)
    tmp = out_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": os.environ["HOME"],
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TMPDIR": str(tmp),
        "SCITEX_STORE_DSN": dsn,
    }
    for i in range(sessions):
        res = subprocess.run(
            [python, str(script)],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if res.returncode != 0:
            raise RuntimeError(f"session {i} exited {res.returncode}:\n{res.stderr}")
    version = subprocess.run(
        [
            python,
            "-c",
            "import importlib.metadata as m; print(m.version('scitex-session'))",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    runs = sorted((project / "scripts" / "probe_out" / "FINISHED_SUCCESS").iterdir())
    store = project.joinpath(".scitex", "session", "environments")
    run_rows = [
        {
            "run": r.name,
            "run_dir_bytes": _tree_bytes(r),
            "config_yaml_bytes": _tree_bytes(r / "CONFIGS" / "CONFIG.yaml"),
            "config_pkl_bytes": _tree_bytes(r / "CONFIGS" / "CONFIG.pkl"),
        }
        for r in runs
    ]
    store_files = sorted(store.glob("*.json")) if store.is_dir() else []
    run_total = sum(r["run_dir_bytes"] for r in run_rows)
    store_total = sum(p.stat().st_size for p in store_files)
    return {
        "scitex_session": version,
        "sessions": sessions,
        "runs": run_rows,
        "env_store_files": [
            {"name": p.name, "bytes": p.stat().st_size} for p in store_files
        ],
        "run_dirs_bytes": run_total,
        "env_store_bytes": store_total,
        "total_bytes": run_total + store_total,
        "project": str(project),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--sessions", type=int, default=3)
    a = p.parse_args(argv)
    dsn = os.environ.get("SCITEX_STORE_DSN")
    if not dsn:
        p.error(
            "SCITEX_STORE_DSN is not set: set it to the store the runs may write to"
        )
    print(json.dumps(measure(a.python, a.out_dir.resolve(), a.sessions, dsn), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# EOF
