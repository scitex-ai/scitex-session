"""Tests for scitex_session._linter_s008._finder — the STX-S008 detector.

The detector is a verbatim port of scitex-dev's ``_magic_numbers.py``; while
the engine still ships its copy, ours must report exactly the same values.
"""

import ast

import pytest

from scitex_session._linter_s008 import find_hardcoded_parameters

SOURCE = (
    "import numpy as np\n"
    "WINDOW = 256\n"
    "def main(alpha=0.05):\n"
    "    rng = np.random.default_rng(42)\n"
    "    if p < 0.01:\n"
    "        return 0\n"
    "    x = welch(sig, fs=512)\n"
    "    return 0\n"
)


def _labels(finder):
    return [(node.lineno, label) for node, label in finder(ast.parse(SOURCE), SOURCE)]


def test_detector_reports_parameter_lines():
    # Arrange
    source = SOURCE
    # Act
    lines = [line for line, _ in _labels(find_hardcoded_parameters)]
    # Assert
    assert lines == sorted(set(lines)) and 2 in lines


def test_detector_matches_engine_copy():
    # Arrange
    engine = pytest.importorskip("scitex_dev.linter._magic_numbers")
    # Act
    ours = _labels(find_hardcoded_parameters)
    # Assert — delete together with the engine copy once the handover ends.
    assert ours == _labels(engine.find_hardcoded_parameters)
