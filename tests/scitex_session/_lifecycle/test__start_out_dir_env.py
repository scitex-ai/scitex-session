#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``SCITEX_SESSION_OUT_DIR`` redirect in ``_resolve_out_base_dir``.

Driver (operator, relayed via neurovista 2026-07-05): large SLURM-array
research workloads exhaust the shared parallel filesystem's (GPFS/Lustre)
inode budget because every run materialises a per-run session tree under
``<script>_out/RUNNING/<ID>/`` next to the script — which lives on the
shared FS. The ``SCITEX_SESSION_OUT_DIR`` knob points the *whole* ``_out``
base at node-local scratch so the entire lifecycle (RUNNING tree, the
running->finished copytree, and any archive) stays off the shared FS.

Default behaviour (env unset) must be byte-for-byte what it was before the
knob existed. These tests pin both branches with the real environment —
no mocks, no monkeypatch.
"""

from __future__ import annotations

import os

import pytest

from scitex_session._lifecycle._start import _OUT_DIR_ENV, _resolve_out_base_dir


@pytest.fixture
def restore_out_dir_env():
    """Save/restore ``SCITEX_SESSION_OUT_DIR`` around a test using the real
    environment (not ``monkeypatch``); starts each test with the var unset."""
    sentinel = object()
    original = os.environ.get(_OUT_DIR_ENV, sentinel)
    os.environ.pop(_OUT_DIR_ENV, None)
    try:
        yield
    finally:
        if original is sentinel:
            os.environ.pop(_OUT_DIR_ENV, None)
        else:
            os.environ[_OUT_DIR_ENV] = original  # type: ignore[assignment]


def test_resolve_out_base_defaults_adjacent_to_script(restore_out_dir_env) -> None:
    # Arrange — env unset (fixture guarantees this); caller lives at /a/b/.
    caller_file = "/a/b/myscript.py"

    # Act
    sdir = _resolve_out_base_dir(caller_file, "ID1234")

    # Assert — output base sits next to the script, unchanged from legacy.
    assert "/a/b/myscript_out/RUNNING/ID1234" in sdir


def test_resolve_out_base_redirects_under_env_dir(restore_out_dir_env) -> None:
    # Arrange — point the whole _out base at node-local scratch.
    os.environ[_OUT_DIR_ENV] = "/scratch/node"
    caller_file = "/home/u/proj/myscript.py"

    # Act
    sdir = _resolve_out_base_dir(caller_file, "ID1234")

    # Assert — base now lives under the scratch dir, not next to the script.
    assert "/scratch/node/myscript_out/RUNNING/ID1234" in sdir


def test_resolve_out_base_preserves_running_id_segment_when_redirected(
    restore_out_dir_env,
) -> None:
    # Arrange
    os.environ[_OUT_DIR_ENV] = "/scratch/node"

    # Act
    sdir = _resolve_out_base_dir("/home/u/proj/myscript.py", "IDabcd")

    # Assert — the RUNNING/<ID> tail is preserved so downstream consumers
    # (SDIR_OUT stripping, running2finished, archiving) are unaffected.
    assert "RUNNING/IDabcd" in sdir


def test_resolve_out_base_ignores_empty_env_value(restore_out_dir_env) -> None:
    # Arrange — an empty value must behave exactly like "unset".
    os.environ[_OUT_DIR_ENV] = ""
    caller_file = "/a/b/myscript.py"

    # Act
    sdir = _resolve_out_base_dir(caller_file, "ID1234")

    # Assert — falls back to the script-adjacent default.
    assert "/a/b/myscript_out/RUNNING/ID1234" in sdir


def test_resolve_out_base_uses_only_basename_stem_when_redirected(
    restore_out_dir_env,
) -> None:
    # Arrange — a deeply nested script; the redirect must key on the bare
    # stem, not the script's full path, so scratch dirs stay shallow.
    os.environ[_OUT_DIR_ENV] = "/scratch"
    caller_file = "/deep/nested/path/analyze.py"

    # Act
    sdir = _resolve_out_base_dir(caller_file, "ID1234")

    # Assert
    assert "/scratch/analyze_out/RUNNING/ID1234" in sdir
