#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__startup_cost.py

"""Start-up cost guards for ``@session`` — robust on CI (no wall-clock).

A session's fixed cost was ~6 s for a script that does nothing (paper E5).
Its causes were structural, so the guards are structural too:

* the provenance capture and the caller walk must INSPECT ``sys.modules``
  without EXECUTING anything in it — a ``getattr(module, "__file__")`` ran
  every lazily-imported module (``importlib.util.LazyLoader``) and every
  moved-module alias (its ``__getattr__`` imports the new path and warns);
* a trivial session must not import heavy libraries it does not use
  (pandas, xarray, ... came in through ``scitex_str`` and the help epilog);
* the header must not pause (it slept 2 s).

Real modules, real files, a real subprocess — no mocks.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from scitex_session._lifecycle._caller import _find_user_caller_file
from scitex_session._lifecycle._provenance import collect_run_metadata


def _lazy_module(path: Path, name: str) -> types.ModuleType:
    """A real ``LazyLoader`` module for ``path``: its code has NOT run yet."""
    spec = importlib.util.spec_from_file_location(name, path)
    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _HookedAlias(types.ModuleType):
    """A moved-module alias: any missing attribute is resolved by a hook."""

    def __init__(self, name: str, touched: list) -> None:
        super().__init__(name)
        object.__setattr__(self, "_touched", touched)

    def __getattr__(self, attr: str):
        object.__getattribute__(self, "_touched").append(attr)
        raise AttributeError(attr)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'p'\n")
    marker = root / "executed.marker"
    (root / "lazy_helper_for_startup.py").write_text(
        f"open({str(marker)!r}, 'w').write('ran')\nVALUE = 1\n"
    )
    return root, marker


def test_environment_capture_does_not_execute_lazy_modules(project):
    # Arrange
    root, marker = project
    lazy = _lazy_module(root / "lazy_helper_for_startup.py", "lazy_helper_for_startup")
    modules = {"lazy_helper_for_startup": lazy}
    # Act
    collect_run_metadata(str(root / "run.py"), modules=modules, project_root=root)
    # Assert
    assert not marker.exists()


def test_environment_capture_still_records_an_unexecuted_lazy_local_module(project):
    # Arrange
    root, _marker = project
    path = root / "lazy_helper_for_startup.py"
    lazy = _lazy_module(path, "lazy_helper_for_startup")
    # Act
    meta = collect_run_metadata(
        str(root / "run.py"),
        modules={"lazy_helper_for_startup": lazy},
        project_root=root,
    )
    # Assert
    assert str(path.resolve()) in meta["local_modules"]


def test_environment_capture_does_not_call_module_getattr_hooks(project):
    # Arrange
    root, _marker = project
    touched: list = []
    modules = {"old_pkg.moved": _HookedAlias("old_pkg.moved", touched)}
    # Act
    collect_run_metadata(str(root / "run.py"), modules=modules, project_root=root)
    # Assert
    assert touched == []


@pytest.fixture
def hooked_alias_in_sys_modules():
    """A hooked alias registered in the REAL ``sys.modules``; removed after."""
    name = "old_pkg_for_walk.moved"
    touched: list = []
    sys.modules[name] = _HookedAlias(name, touched)
    yield touched
    sys.modules.pop(name, None)


def test_caller_walk_does_not_call_module_getattr_hooks(hooked_alias_in_sys_modules):
    # Arrange
    touched = hooked_alias_in_sys_modules
    # Act
    _find_user_caller_file()
    # Assert
    assert touched == []


def test_caller_walk_finds_the_calling_test_file():
    # Arrange
    expected = os.path.abspath(__file__)
    # Act
    found = _find_user_caller_file()
    # Assert
    assert os.path.abspath(found) == expected


#: Libraries a do-nothing session has no reason to import. Each was imported
#: by session start-up before (via ``scitex_str``, the ``--help`` epilog, or
#: lazy modules the environment capture executed).
_HEAVY = ("pandas", "xarray", "pyarrow", "scipy", "playwright", "docx", "torch")

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


@pytest.fixture(scope="module")
def trivial_session_report(tmp_path_factory):
    """Run a do-nothing ``@session`` script once in a fresh interpreter."""
    root = tmp_path_factory.mktemp("trivial")
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


@pytest.mark.parametrize("heavy", _HEAVY)
def test_trivial_session_does_not_import_heavy_library(trivial_session_report, heavy):
    # Arrange
    imported = {m.split(".", 1)[0] for m in trivial_session_report["modules"]}
    # Act
    found = heavy in imported
    # Assert
    assert not found, f"{heavy} imported by a do-nothing @session run"


def test_trivial_session_triggers_no_moved_module_deprecation(trivial_session_report):
    # Arrange
    report = trivial_session_report
    # Act
    deprecations = report["deprecations"]
    # Assert
    assert deprecations == []


@pytest.fixture
def site_dir_on_sys_path(tmp_path):
    """A real ``site-packages`` directory holding an importable package."""
    site = tmp_path / "site-packages"
    (site / "startup_cost_pkg").mkdir(parents=True)
    (site / "startup_cost_pkg" / "__init__.py").write_text("VALUE = 1\n")
    sys.path.insert(0, str(site))
    yield site
    sys.path.remove(str(site))


def _install_dist_info(site: Path) -> None:
    info = site / "startup_cost_pkg-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: startup-cost-pkg\nVersion: 1.0\n"
    )
    (info / "top_level.txt").write_text("startup_cost_pkg\n")
    (info / "RECORD").write_text("startup_cost_pkg/__init__.py,,\n")


def test_distribution_installed_mid_run_is_attributed_at_close(site_dir_on_sys_path):
    # Arrange: the start-time scan sees no distribution for the package ...
    from scitex_session._lifecycle._environment import _imported_distributions

    site = site_dir_on_sys_path
    module = _lazy_module(site / "startup_cost_pkg" / "__init__.py", "startup_cost_pkg")
    modules = {"startup_cost_pkg": module}
    _imported_distributions(modules, None)
    _install_dist_info(site)  # ... then it is installed during the run
    # Act
    dists, _unresolved = _imported_distributions(modules, None)
    # Assert
    assert "startup-cost-pkg" in dists
