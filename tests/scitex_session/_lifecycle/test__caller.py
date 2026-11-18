#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__caller.py

"""Caller walk (:mod:`scitex_session._lifecycle._caller`).

The walk used ``inspect.stack()`` + ``inspect.getmodule``, whose first call
probes ``__file__`` on every ``sys.modules`` entry and so executed lazily
loaded modules and moved-module aliases at every session start. It must
resolve the user's file without touching module attribute hooks.
Real modules — no mocks.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

from scitex_session._lifecycle._caller import _find_user_caller_file


class _HookedAlias(types.ModuleType):
    """A moved-module alias: any missing attribute is resolved by a hook."""

    def __init__(self, name: str, touched: list) -> None:
        super().__init__(name)
        object.__setattr__(self, "_touched", touched)

    def __getattr__(self, attr: str):
        object.__getattribute__(self, "_touched").append(attr)
        raise AttributeError(attr)


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
