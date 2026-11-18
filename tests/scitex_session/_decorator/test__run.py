#!/usr/bin/env python3
# File: ./tests/scitex_session/_decorator/test__run.py

"""``@session`` CLI run path (:mod:`scitex_session._decorator._run`).

Start-up cost guard, robust on CI (no wall-clock): a do-nothing ``@session``
script took ~6 s (paper-scitex-clew E5) because start-up imported heavy
libraries it never used (pandas/xarray via ``scitex_str``, the ``--help``
epilog built every run, lazy modules executed by the environment capture)
and fired moved-module deprecation warnings into every run log. Each check
runs the script in a fresh interpreter. Real subprocess — no mocks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

#: Libraries a do-nothing session has no reason to import; each was imported
#: by session start-up before.
_HEAVY = frozenset(
    {"pandas", "xarray", "pyarrow", "scipy", "playwright", "docx", "torch"}
)

_TRIVIAL = textwrap.dedent(
    """
    import json, sys, warnings
    from pathlib import Path

    REPORT = Path(__file__).with_suffix(".json")
    # Session cost only: no provenance observer, so no store is needed.
    sys.modules["scitex_clew"] = None
    warnings.simplefilter("always", DeprecationWarning)
    _caught = []
    warnings.showwarning = lambda m, c, *a, **k: _caught.append(str(m))

    import scitex_session

    @scitex_session.session(verbose=False)
    def main(CONFIG=scitex_session.INJECTED):
        return 0

    if __name__ == "__main__":
        main()
        REPORT.write_text(json.dumps({
            "modules": sorted(sys.modules),
            "deprecations": [w for w in _caught if "has moved to" in w],
        }))
    """
)


def _run_trivial_session(root: Path) -> dict:
    """Run a do-nothing ``@session`` script in a fresh interpreter."""
    (root / "pyproject.toml").write_text("[project]\nname = 't'\n")
    script = root / "trivial.py"
    script.write_text(_TRIVIAL)
    keep = ("PATH", "HOME", "LANG", "SYSTEMROOT", "COVERAGE_PROCESS_START")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["MPLBACKEND"] = "Agg"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(script.with_suffix(".json").read_text())


def test_trivial_session_imports_no_heavy_library(tmp_path):
    # Arrange
    report = _run_trivial_session(tmp_path)
    # Act
    imported = {m.split(".", 1)[0] for m in report["modules"]}
    # Assert
    assert sorted(imported & _HEAVY) == []


def test_trivial_session_triggers_no_moved_module_deprecation(tmp_path):
    # Arrange
    report = _run_trivial_session(tmp_path)
    # Act
    deprecations = report["deprecations"]
    # Assert
    assert deprecations == []
