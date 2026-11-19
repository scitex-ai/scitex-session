"""Tests for scitex_session._linter_plugin — the @stx.session structure rules.

STX-S001/S003/S004/S006 moved here from the scitex-dev engine with no
behaviour change, so these tests pin the engine's exact behaviour and the
metadata identical to the engine's compatibility copy.
"""

import ast

from scitex_dev.linter import spi
from scitex_dev.linter.config import LinterConfig

from scitex_session._linter_plugin import _bind_checker, get_plugin, make_rules

_IDS = ("STX-S001", "STX-S003", "STX-S004", "STX-S006")


class _Checker(_bind_checker(make_rules(spi.Rule), spi)):
    # The umbrella `scitex` (the rules' `requires`) may be absent in this
    # env; pin availability so the checker's own logic is what is tested.
    available = {"scitex": True}


def _findings(source, filepath="run_analysis.py", config=None):
    checker = _Checker(source.splitlines(), config or LinterConfig(), filepath=filepath)
    checker.visit(ast.parse(source))
    return [(i.rule.id, i.line) for i in checker.issues]


_FULL_SIGNATURE = (
    "CONFIG=stx.session.INJECTED, plt=stx.session.INJECTED, "
    "COLORS=stx.session.INJECTED, rngg=stx.session.INJECTED, "
    "logger=stx.session.INJECTED"
)


def test_plugin_rules_match_engine_compatibility_copy():
    # Arrange
    from scitex_dev.linter.rules import ALL_RULES

    # Act
    declared = {r.id: r for r in get_plugin()["rules"]}
    # Assert — delete together with the engine copy once the handover ends.
    assert declared == {rid: ALL_RULES[rid] for rid in _IDS}


def test_main_guard_without_session_decorator_reports_s001():
    # Arrange
    source = "def main():\n    return 0\n\nif __name__ == '__main__':\n    main()\n"
    # Act
    found = _findings(source)
    # Assert
    assert found == [("STX-S001", 1)]


def test_module_decorator_satisfies_s001():
    # Arrange
    source = (
        "import scitex as stx\n@stx.module(name='m')\ndef main():\n    return 0\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    # Act
    found = _findings(source)
    # Assert
    assert found == []


def test_argparse_in_script_reports_s003_per_statement():
    # Arrange
    source = "import argparse\nfrom argparse import ArgumentParser\n"
    # Act
    found = _findings(source)
    # Assert
    assert found == [("STX-S003", 1), ("STX-S003", 2)]


def test_argparse_in_library_code_is_not_reported():
    # Arrange
    source = "import argparse\n"
    # Act
    found = _findings(source, filepath="src/pkg/cli.py")
    # Assert
    assert found == []


def test_session_without_int_return_reports_s004():
    # Arrange
    source = f"import scitex as stx\n@stx.session\ndef main({_FULL_SIGNATURE}):\n    return None\n"
    # Act
    found = _findings(source, filepath="src/pkg/x.py")
    # Assert
    assert found == [("STX-S004", 3)]


def test_missing_injected_params_message_names_them():
    # Arrange
    source = (
        "import scitex as stx\n@stx.session\ndef main(CONFIG, plt):\n    return 0\n"
    )
    checker = _Checker(source.splitlines(), LinterConfig(), filepath="src/pkg/x.py")
    # Act
    checker.visit(ast.parse(source))
    # Assert
    assert checker.issues[0].rule.message == (
        "@stx.session function missing INJECTED parameters: COLORS, logger, rngg. "
        "All 5 must be declared: CONFIG, COLORS, logger, plt, rngg"
    )


def test_keyword_only_injected_params_count_as_declared():
    # Arrange
    source = (
        f"import scitex as stx\n@stx.session\nasync def main(*, {_FULL_SIGNATURE}):\n"
        "    return 0\n"
    )
    # Act
    found = _findings(source, filepath="src/pkg/x.py")
    # Assert
    assert found == []


def test_disabled_rule_is_not_reported():
    # Arrange
    config = LinterConfig(disable=["STX-S003"])
    # Act
    found = _findings("import argparse\n", config=config)
    # Assert
    assert found == []
