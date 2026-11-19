"""scitex-dev linter plugin: the ``@stx.session`` structure rules.

These rules enforce scitex-session's own API — the decorator, its CLI
generation, its integer exit code and its INJECTED parameters — so they live
here, next to that API (scitex-dev ``08_linter-plugins.md``; ownership table
``docs/rule-ownership.md`` in scitex-dev):

- ``STX-S001`` — a script's ``main()`` lacks ``@stx.session`` / ``@stx.module``
- ``STX-S003`` — ``argparse`` in a script (the session generates the CLI)
- ``STX-S004`` — a session function returns no integer exit code
- ``STX-S006`` — a session function does not declare every INJECTED parameter

They moved from the scitex-dev engine WITHOUT a behaviour change: same ids,
severities, categories, messages, suggestions and ``requires`` gates, and
the same emission gates (``spi.add_issue``). scitex-dev's handover table
names scitex-session as their owner, so when this plugin is installed the
engine stops emitting them; with an older scitex-session the engine's
compatibility copy keeps emitting them.

``STX-S002`` (``__main__`` guard), ``STX-S005`` (``import scitex as stx``),
``STX-S007`` and ``STX-S008`` stay in scitex-dev — see the ownership table
for why.
"""

from __future__ import annotations

import ast
from dataclasses import replace

__all__ = ["get_plugin", "make_rules"]

# Decorator namespaces the engine has always recognised for @<ns>.session /
# @<ns>.module. Kept verbatim: widening it would change findings.
_DECO_NAMESPACES = ("stx", "scitex", "scitex_io")


def make_rules(Rule):
    """Build the four session-structure rules from scitex-dev's ``Rule``."""
    S001 = Rule(
        id="STX-S001",
        severity="error",
        category="structure",
        message=(
            "Missing @stx.session/@stx.module on main() — its inputs/outputs are "
            "then UNTRACKED, breaking data lineage (reproducibility / clew provenance)"
        ),
        suggestion=(
            "@stx.session RECORDS this script's inputs/outputs so its data lineage is\n"
            "tracked. This is a hard reproducibility / clew-provenance requirement, NOT\n"
            "a style nit: without it, `clew chain` on your outputs returns length 0 (no\n"
            "producing-session edge links them to this script).\n"
            "  @stx.session\n"
            "  def main(...):\n"
            "      return 0\n"
            "Use @stx.module for cloud modules.\n"
            "What @stx.session wires up (see `help(scitex.session.start)` / the "
            "scitex 'session' skill): provenance/clew lineage, run logging, CONFIG "
            "load, reproducible RNG, figrecipe-styled plt + recorded saves.\n"
            "If this script must NOT be a session (instrumentation that would record\n"
            "itself into the store it measures), opt out auditably in its header:\n"
            "  # stx-allow-file: STX-S001  (reason: <why it must not be a session>)\n"
            "If this is library code (not a script), add its directory to library_dirs:\n"
            "  [tool.scitex-linter]\n"
            '  library_dirs = ["src", "tests", "apps", "config", "docs"]\n'
            "  Or: SCITEX_DEV_LINTER_NON_SCRIPT_DIRS=src,tests,apps,config,docs"
        ),
        requires="scitex",
    )
    S003 = Rule(
        id="STX-S003",
        severity="error",
        category="structure",
        message="argparse detected — @stx.session auto-generates CLI from function signature",
        suggestion=(
            "Remove `import argparse` and define CLI inputs as function arguments:\n"
            "  @stx.session\n"
            '  def main(data_path: str, subject: str = "sub-01"):\n'
            "      # Auto-generates: --data-path, --subject\n"
            "Numeric analysis parameters belong in config/*.yaml (CONFIG), not in\n"
            "signature defaults — see STX-S008."
        ),
        requires="scitex",
    )
    S004 = Rule(
        id="STX-S004",
        severity="warning",
        category="structure",
        message="@stx.session function should return an integer exit code",
        suggestion="Add `return 0` for success at the end of your session function.",
        requires="scitex",
    )
    S006 = Rule(
        id="STX-S006",
        severity="warning",
        category="structure",
        message="@stx.session function missing explicit INJECTED parameters",
        suggestion=(
            "Declare auto-injected values explicitly in the function signature:\n"
            "  @stx.session\n"
            "  def main(\n"
            "      CONFIG=stx.session.INJECTED,\n"
            "      plt=stx.session.INJECTED,\n"
            "      COLORS=stx.session.INJECTED,\n"
            "      rngg=stx.session.INJECTED,\n"
            "      logger=stx.session.INJECTED,\n"
            "  ):\n"
            "      return 0"
        ),
        requires="scitex",
    )
    return S001, S003, S004, S006


def _is_session_decorated(node) -> bool:
    """``@stx.session`` (any recognised namespace) or a bare ``@session``."""
    for deco in node.decorator_list:
        if (
            isinstance(deco, ast.Attribute)
            and isinstance(deco.value, ast.Name)
            and deco.value.id in _DECO_NAMESPACES
            and deco.attr == "session"
        ):
            return True
        if isinstance(deco, ast.Name) and deco.id == "session":
            return True
    return False


def _is_module_decorated(node) -> bool:
    """``@stx.module`` / ``@stx.module(...)`` / bare ``@module(...)``."""
    for deco in node.decorator_list:
        if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
            if (
                isinstance(deco.func.value, ast.Name)
                and deco.func.value.id in _DECO_NAMESPACES
                and deco.func.attr == "module"
            ):
                return True
        if (
            isinstance(deco, ast.Attribute)
            and isinstance(deco.value, ast.Name)
            and deco.value.id in _DECO_NAMESPACES
            and deco.attr == "module"
        ):
            return True
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Name)
            and deco.func.id == "module"
        ):
            return True
    return False


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _returns_int(node) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Return)
            and child.value is not None
            and isinstance(child.value, ast.Constant)
            and isinstance(child.value.value, int)
        ):
            return True
    return False


def _bind_checker(rules, spi):
    S001, S003, S004, S006 = rules

    class SessionStructureChecker(ast.NodeVisitor):
        """Emit STX-S001/S003/S004/S006 for one module."""

        accepts_filepath = True
        category = "structure"
        # Package-availability map for the ``requires`` gate; None = detect.
        # A seam for tests that must see findings without the umbrella.
        available = None

        def __init__(self, source_lines, config, filepath="<stdin>"):
            self.source_lines = source_lines
            self.config = config
            self.filepath = filepath
            self.issues = []
            self._is_script = spi.is_script(filepath, config)
            self._has_main_guard = False
            self._has_entry_decorator = False

        def _source(self, lineno):
            if 1 <= lineno <= len(self.source_lines):
                return self.source_lines[lineno - 1].rstrip()
            return ""

        def _emit(self, rule, line, col, source_line):
            spi.add_issue(
                self.issues,
                rule,
                line,
                col,
                source_line,
                self.config,
                available=self.available,
            )

        def visit_Module(self, node):
            self.generic_visit(node)
            # S001 — decided once the whole module is seen (the guard and
            # main() can appear in any order).
            if (
                self._is_script
                and self._has_main_guard
                and not self._has_entry_decorator
            ):
                self._emit(S001, 1, 0, "")

        def visit_Import(self, node):
            for alias in node.names:
                if alias.name == "argparse" and self._is_script:
                    self._emit(
                        S003, node.lineno, node.col_offset, self._source(node.lineno)
                    )
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if (node.module or "") == "argparse" and self._is_script:
                self._emit(
                    S003, node.lineno, node.col_offset, self._source(node.lineno)
                )
            self.generic_visit(node)

        def visit_If(self, node):
            if _is_main_guard(node):
                self._has_main_guard = True
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if _is_session_decorated(node):
                self._has_entry_decorator = True
                line = self._source(node.lineno)
                if not _returns_int(node):
                    self._emit(S004, node.lineno, node.col_offset, line)
                self._check_injected(node, line)
            elif _is_module_decorated(node):
                self._has_entry_decorator = True
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def _check_injected(self, node, line):
            args = node.args
            declared = {
                arg.arg
                for arg in (
                    list(args.args)
                    + list(args.kwonlyargs)
                    + list(getattr(args, "posonlyargs", []))
                )
            }
            missing = sorted(set(self.config.required_injected) - declared)
            if missing:
                rule = replace(
                    S006,
                    message=(
                        "@stx.session function missing INJECTED parameters: "
                        f"{', '.join(missing)}. "
                        "All 5 must be declared: CONFIG, COLORS, logger, plt, rngg"
                    ),
                )
                self._emit(rule, node.lineno, node.col_offset, line)

    return SessionStructureChecker


def get_plugin():
    """Return the session-structure rules and their checker."""
    from scitex_dev.linter import spi

    rules = make_rules(spi.Rule)
    return {
        "rules": list(rules),
        "call_rules": {},
        "axes_hints": {},
        "checkers": [_bind_checker(rules, spi)],
    }


# EOF
