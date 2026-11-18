#!/usr/bin/env python3
"""Compile-only smoke test for examples/quickstart.py."""

import py_compile
import re
from pathlib import Path

import yaml

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "quickstart.py"
QUICKSTART_YAML = EXAMPLE.parent / "config" / "QUICKSTART.yaml"


def test_quickstart_example_file_exists_on_disk():
    # Arrange
    # Act
    is_file = EXAMPLE.is_file()
    # Assert
    assert is_file, f"missing example: {EXAMPLE}"


def test_quickstart_example_compiles_without_error():
    # Arrange
    # Act
    py_compile.compile(str(EXAMPLE), doraise=True)
    # Assert
    assert True  # no PyCompileError raised


def test_quickstart_reads_every_parameter_it_uses_from_config_yaml():
    # Arrange
    source = EXAMPLE.read_text(encoding="utf-8")
    config = yaml.safe_load(QUICKSTART_YAML.read_text(encoding="utf-8"))
    # Act
    used_keys = set(re.findall(r"\bCONFIG\.QUICKSTART\.([A-Z][A-Z0-9_]*)", source))
    # Assert
    assert used_keys and used_keys <= set(config), (
        f"keys used by the example {sorted(used_keys)} must all be defined in "
        f"{QUICKSTART_YAML} (found {sorted(config)})"
    )


# EOF
