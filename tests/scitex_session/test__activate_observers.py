#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for import-time observer activation (the ``scitex_session.observers``
scan).

scitex_session discovers observer registrars via the
``scitex_session.observers`` entry-point group and calls each on import, so a
subscriber (e.g. scitex-clew) self-activates from ``import scitex_session``
alone without scitex_session ever importing it by name. ``_activate_observers``
takes the registrars as a parameter (real dependency injection — no fixture
patches production internals); these tests pass hand-rolled
``(name, registrar)`` pairs to verify the scan invokes each and isolates a
raising one.
"""

from __future__ import annotations

import scitex_session


def test_activate_observers_invokes_each_registered_registrar():
    # Arrange
    called = []
    observers = [("clew", lambda: called.append("clew") or True)]
    # Act
    scitex_session._activate_observers(observers)
    # Assert
    assert called == ["clew"]


def test_activate_observers_isolates_a_raising_registrar():
    # Arrange
    survived = []

    def _boom():
        raise RuntimeError("observer registration failed")

    observers = [("boom", _boom), ("ok", lambda: survived.append("ok"))]
    # Act
    scitex_session._activate_observers(observers)
    # Assert
    assert survived == ["ok"]
