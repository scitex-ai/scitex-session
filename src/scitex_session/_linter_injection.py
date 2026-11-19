"""scitex-dev linter rules for the ``@stx.session`` injection contract.

What the session decorator hands the function — ``rngg``, ``plt``,
``logger`` — is this package's API, so the rules that say "use the injected
one" live here:

- ``STX-I006`` — ``import random`` (use the injected ``rngg``)
- ``STX-P010`` — top-level ``figrecipe`` in a script whose ``main`` is an
  ``@stx.session`` (use the injected ``plt``)
- ``STX-P005`` — ``print()`` once an ``@stx.session`` function has been
  seen, outside ``src/`` (use the injected ``logger``)

Moved from the scitex-dev engine (I006, P010, and the P005 emitter; P005's
declaration moves from figrecipe) with NO behaviour change: metadata is
verbatim and detection mirrors the engine exactly, including its
order-dependence (P005 fires for ``print`` calls visited after the first
session-decorated function is entered, as the engine's visitor did).
scitex-dev's handover table names scitex-session as the owner, so the engine
stops emitting these ids — and figrecipe's P005 declaration is superseded —
when this plugin is installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

__all__ = ["I006_SPEC", "P005_SPEC", "P010_SPEC", "bind_injection_checker"]

_STX_NAMESPACES = ("stx", "scitex", "scitex_io")

I006_SPEC = dict(
    id="STX-I006",
    severity="info",
    category="import",
    message="Use `rngg` (injected by @stx.session) for reproducible randomness",
    suggestion="Remove `import random` and use `rngg` from @stx.session injection.",
    requires="scitex",
)

P010_SPEC = dict(
    id="STX-P010",
    severity="warning",
    category="plot",
    message=(
        "top-level `figrecipe` used inside @stx.session — use the "
        "session-injected `plt` (e.g. `plt.subplots()`) instead"
    ),
    suggestion=(
        "Use the session-injected `plt.subplots` instead of top-level "
        "figrecipe:\n"
        "  @stx.session\n"
        "  def main(..., plt=stx.session.INJECTED):\n"
        "      fig, ax = plt.subplots()   # figrecipe-backed, wired to the\n"
        "                                 # session output dir\n"
        "Font sizes come from SCITEX_STYLE — drop per-call `fontsize=` "
        "(see STX-P007). Set labels with `ax.set_xyt(x, y, t)` (STX-FM010)."
    ),
    requires="scitex",
)

P005_SPEC = dict(
    id="STX-P005",
    severity="info",
    category="plot",
    message="`print()` inside @stx.session — use `logger` for tracked logging",
    suggestion="Replace `print(msg)` with `logger.info(msg)` (injected by @stx.session).",
    requires="scitex",
)


def _is_figrecipe_module(name) -> bool:
    return name == "figrecipe" or (name or "").startswith("figrecipe.")


def bind_injection_checker(I006, P010, P005, spi, is_session_decorated):
    """Return the checker class emitting I006 / P010 / P005."""

    class SessionInjectionChecker(ast.NodeVisitor):
        accepts_filepath = True
        category = "import"
        # Package-availability map for the ``requires`` gate; None = detect.
        available = None

        def __init__(self, source_lines, config, filepath="<stdin>"):
            self.source_lines = source_lines
            self.config = config
            self.filepath = filepath
            self.issues = []
            self._is_script = spi.is_script(filepath, config)
            self._in_src = "src" in Path(filepath).parts
            self._imports = {}  # alias -> module path (as the engine records)
            self._has_session = False
            self._figrecipe_usages = []

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
            # P010 — only for scripts whose module declares a session main;
            # otherwise top-level figrecipe is the correct API.
            if self._is_script and self._has_session:
                for line, col, src in self._figrecipe_usages:
                    self._emit(P010, line, col, src)

        def visit_Import(self, node):
            line = self._source(node.lineno)
            for alias in node.names:
                self._imports[alias.asname or alias.name] = alias.name
                if alias.name == "random":
                    self._emit(I006, node.lineno, node.col_offset, line)
                if _is_figrecipe_module(alias.name):
                    self._figrecipe_usages.append((node.lineno, node.col_offset, line))
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            module = node.module or ""
            for alias in node.names:
                self._imports[alias.asname or alias.name] = f"{module}.{alias.name}"
            if _is_figrecipe_module(module):
                line = self._source(node.lineno)
                self._figrecipe_usages.append((node.lineno, node.col_offset, line))
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if is_session_decorated(node):
                self._has_session = True
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            func = node.func
            if isinstance(func, ast.Attribute):
                self._record_figrecipe_call(node, func)
            elif (
                isinstance(func, ast.Name)
                and func.id == "print"
                and self._has_session
                and not self._in_src
            ):
                self._emit(
                    P005, node.lineno, node.col_offset, self._source(node.lineno)
                )
            self.generic_visit(node)

        def _record_figrecipe_call(self, node, func):
            mod_name = None
            if isinstance(func.value, ast.Name):
                mod_name = func.value.id
            elif isinstance(func.value, ast.Attribute) and isinstance(
                func.value.value, ast.Name
            ):
                mod_name = func.value.attr
            # stx.* / scitex.* calls are the SciTeX API, never figrecipe use.
            if mod_name in _STX_NAMESPACES or (
                isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id in _STX_NAMESPACES
            ):
                return
            root = func
            while isinstance(root, ast.Attribute):
                root = root.value
            if not isinstance(root, ast.Name):
                return
            if self._imports.get(root.id, root.id) == "figrecipe":
                line = self._source(node.lineno)
                self._figrecipe_usages.append((node.lineno, node.col_offset, line))

    return SessionInjectionChecker


# EOF
