#!/usr/bin/env python3
# File: ./tests/scitex_session/_lifecycle/test__notebook.py

"""Notebook code identity + the outside-IPython defaults (no kernel needed).

The code hash is a CROSS-PACKAGE contract: scitex-clew recomputes it to
verify a notebook, so the conformance vector below is duplicated verbatim in
scitex-clew's ``tests/scitex_clew/test__notebook.py``. Change both or neither.
"""

from __future__ import annotations

import json

from scitex_session._lifecycle._caller import _cli_argv, _find_user_caller
from scitex_session._lifecycle._notebook import (
    CODE_HASH_SCHEME,
    is_cell_file,
    notebook_code_cells,
    notebook_code_sha256,
)

#: ipynb-code-v1 conformance vector (shared with scitex-clew).
VECTOR_NOTEBOOK = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {},
    "cells": [
        {"cell_type": "code", "source": "a = 1\r\nb = 2", "metadata": {},
         "outputs": [], "execution_count": None},
        {"cell_type": "markdown", "source": "# x", "metadata": {}},
        {"cell_type": "code", "source": ["print(a)\n", 'print("é")'], "metadata": {},
         "outputs": [{"output_type": "stream", "name": "stdout", "text": "1"}],
         "execution_count": 7},
    ],
}  # fmt: skip
VECTOR_SHA256 = "cf7e2cc7afc91f1334d8d14aad99644cda863f2173227baed69996d2140a5d3d"


def _write(tmp_path, nb) -> str:
    path = tmp_path / "v.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")
    return str(path)


def test_code_hash_matches_the_shared_conformance_vector(tmp_path):
    # Arrange
    path = _write(tmp_path, VECTOR_NOTEBOOK)
    # Act
    digest = notebook_code_sha256(path)
    # Assert
    assert digest == VECTOR_SHA256


def test_code_cells_skip_markdown_and_normalise_newlines(tmp_path):
    # Arrange
    path = _write(tmp_path, VECTOR_NOTEBOOK)
    # Act
    cells = notebook_code_cells(path)
    # Assert
    assert cells == ["a = 1\nb = 2", 'print(a)\nprint("é")']


def test_code_hash_ignores_outputs_counts_and_metadata(tmp_path):
    # Arrange
    nb = json.loads(json.dumps(VECTOR_NOTEBOOK))
    nb["cells"][2]["outputs"] = []
    nb["cells"][2]["execution_count"] = None
    nb["metadata"] = {"kernelspec": {"name": "python3"}}
    # Act
    digest = notebook_code_sha256(_write(tmp_path, nb))
    # Assert
    assert digest == VECTOR_SHA256


def test_code_hash_scheme_name_is_stable():
    # Arrange
    expected = "ipynb-code-v1"
    # Act
    scheme = CODE_HASH_SCHEME
    # Assert
    assert scheme == expected


def test_cli_argv_is_sys_argv_outside_ipython():
    # Arrange
    invocation = None
    # Act
    argv = _cli_argv(invocation)
    # Assert
    assert argv is None


def test_no_file_is_a_cell_outside_ipython():
    # Arrange
    name = "/tmp/ipykernel_1/123.py"
    # Act
    cell = is_cell_file(name)
    # Assert
    assert cell is False


def test_caller_outside_ipython_is_this_file_with_no_invocation():
    # Arrange
    here = __file__
    # Act
    caller = _find_user_caller()
    # Assert
    assert caller == (here, None)


# EOF
