"""Tests for scitex_session._linter_injection — STX-I006 / STX-P010 / STX-P005.

These moved here from the scitex-dev engine (P005's declaration from
figrecipe) with no behaviour change; the tests pin the engine's behaviour,
including its order-dependence for P005.
"""

import ast

from scitex_dev.linter import spi
from scitex_dev.linter.config import LinterConfig

from scitex_session._linter_injection import (
    I006_SPEC,
    P005_SPEC,
    P010_SPEC,
    bind_injection_checker,
)
from scitex_session._linter_plugin import _is_session_decorated

_RULES = tuple(spi.Rule(**s) for s in (I006_SPEC, P010_SPEC, P005_SPEC))


class _Checker(bind_injection_checker(*_RULES, spi, _is_session_decorated)):
    # The rules' `requires` (umbrella scitex) may be absent here.
    available = {"scitex": True}


def _findings(source, filepath="run_x.py"):
    checker = _Checker(source.splitlines(), LinterConfig(), filepath=filepath)
    checker.visit(ast.parse(source))
    return sorted((i.rule.id, i.line) for i in checker.issues)


_SESSION = (
    "import scitex as stx\n"
    "import figrecipe as fr\n"
    "def helper():\n"
    "    print('before')\n"
    "@stx.session\n"
    "def main():\n"
    "    print('inside')\n"
    "    fr.subplots()\n"
    "    stx.plt.subplots()\n"
    "    return 0\n"
)


def test_engine_metadata_is_unchanged_for_moved_engine_rules():
    # Arrange
    from scitex_dev.linter.rules import ALL_RULES

    # Act
    ours = {r.id: r for r in _RULES if r.id in ("STX-I006", "STX-P010")}
    # Assert — delete together with the engine copy once the handover ends.
    assert ours == {rid: ALL_RULES[rid] for rid in ours}


def test_import_random_reports_i006():
    # Arrange
    source = "import os, random\n"
    # Act
    found = _findings(source)
    # Assert
    assert found == [("STX-I006", 1)]


def test_session_script_reports_p010_and_p005_like_the_engine():
    # Arrange
    source = _SESSION
    # Act
    found = _findings(source)
    # Assert — the helper's print precedes the session: not reported.
    assert found == [("STX-P005", 7), ("STX-P010", 2), ("STX-P010", 8)]


def test_library_file_reports_neither_p010_nor_print_in_src():
    # Arrange
    source = _SESSION
    # Act
    found = _findings(source, filepath="src/pkg/run.py")
    # Assert
    assert found == []


def test_no_session_means_top_level_figrecipe_is_fine():
    # Arrange
    source = "import figrecipe as fr\nfr.subplots()\nprint('x')\n"
    # Act
    found = _findings(source)
    # Assert
    assert found == []
