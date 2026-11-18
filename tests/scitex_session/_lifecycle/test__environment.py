#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__environment.py

"""Environment capture (:mod:`scitex_session._lifecycle._environment`).

Start-up cost guards, robust on CI (no wall-clock): the capture must INSPECT
``sys.modules`` without EXECUTING anything in it. A ``getattr(module,
"__file__")`` ran every lazily-imported module (``importlib.util.LazyLoader``)
and every moved-module alias (its ``__getattr__`` imports the new path and
warns) — seconds of every ``@session`` start-up (paper-scitex-clew E5).
The distribution index is reused at close, so a distribution installed
during the run must still be attributed.

Real modules, real files — no mocks.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from scitex_session._lifecycle._environment import _imported_distributions
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
    (root / "lazy_helper_for_env.py").write_text(
        f"open({str(marker)!r}, 'w').write('ran')\nVALUE = 1\n"
    )
    return root, marker


def test_environment_capture_does_not_execute_lazy_modules(project):
    # Arrange
    root, marker = project
    lazy = _lazy_module(root / "lazy_helper_for_env.py", "lazy_helper_for_env")
    modules = {"lazy_helper_for_env": lazy}
    # Act
    collect_run_metadata(str(root / "run.py"), modules=modules, project_root=root)
    # Assert
    assert not marker.exists()


def test_environment_capture_still_records_an_unexecuted_lazy_local_module(project):
    # Arrange
    root, _marker = project
    path = root / "lazy_helper_for_env.py"
    lazy = _lazy_module(path, "lazy_helper_for_env")
    # Act
    meta = collect_run_metadata(
        str(root / "run.py"),
        modules={"lazy_helper_for_env": lazy},
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
def site_dir_on_sys_path(tmp_path):
    """A real ``site-packages`` directory holding an importable package."""
    site = tmp_path / "site-packages"
    (site / "env_capture_pkg").mkdir(parents=True)
    (site / "env_capture_pkg" / "__init__.py").write_text("VALUE = 1\n")
    sys.path.insert(0, str(site))
    yield site
    sys.path.remove(str(site))


def _install_dist_info(site: Path) -> None:
    info = site / "env_capture_pkg-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: env-capture-pkg\nVersion: 1.0\n"
    )
    (info / "top_level.txt").write_text("env_capture_pkg\n")
    (info / "RECORD").write_text("env_capture_pkg/__init__.py,,\n")


def test_distribution_installed_mid_run_is_attributed_at_close(site_dir_on_sys_path):
    # Arrange: the start-time scan sees no distribution for the package ...
    site = site_dir_on_sys_path
    module = _lazy_module(site / "env_capture_pkg" / "__init__.py", "env_capture_pkg")
    modules = {"env_capture_pkg": module}
    _imported_distributions(modules, None)
    _install_dist_info(site)  # ... then it is installed during the run
    # Act
    dists, _unresolved = _imported_distributions(modules, None)
    # Assert
    assert "env-capture-pkg" in dists
