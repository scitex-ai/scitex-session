#!/usr/bin/env python3
# File: ./src/scitex_session/_hooks.py
# ----------------------------------------
"""In-process lifecycle-hook registry for scitex-session.

This is the observer seam that keeps the dependency graph acyclic. Downstream
packages (e.g. scitex-clew, which records session lineage) SUBSCRIBE to session
lifecycle events instead of session importing them by name. It mirrors the
scitex-io post-save/-load hook seam: the producer exposes a hook point and
never names the consumer; the consumer subscribes lazily (clew installs a
``sys.meta_path`` finder that registers on ``import scitex_session``). Direction
of dependency is therefore always ``observer -> scitex_session``, never the
reverse.

Contract (stable, do not change without coordinating with subscribers):

- ``register_session_start_hook(fn)`` — ``fn`` is called at session start as
  ``fn(session_id, script_path, metadata)`` (positional).
- ``register_session_close_hook(fn)`` — ``fn`` is called at session close as
  ``fn(status, exit_code)`` (positional), plus ``metadata=<dict>`` as a
  keyword when ``fn`` accepts it (a ``metadata`` parameter or ``**kwargs``).
  The close ``metadata`` is the close-time part of the run record
  (``scitex_session._lifecycle._provenance.collect_close_metadata``):
  ``packages`` / ``local_modules`` / ``unresolved_imports`` re-collected at
  close (imports made during the run), and ``relocations`` /
  ``sdir_run_final``. Close hooks fire AFTER the session moved its run
  directory from ``RUNNING/<ID>`` to ``FINISHED_*/<ID>``; ``relocations``
  (``[{"from": old_dir, "to": new_dir, ...}]``, real paths) tells the
  observer that every path it recorded under ``from`` now lives under
  ``to``.

Every hook is fired in isolation — a misbehaving subscriber can never break a
session or block the hooks registered after it — but a failure is NEVER
silent: it is logged as a WARNING naming the hook, and ``_fire_*`` returns one
failure record per raising hook. The session stores those records in the
run's own metadata (``CONFIG["_HOOK_FAILURES"]`` → ``CONFIGS/CONFIG.yaml``) and
summarises them at close, so an incomplete provenance record is visible.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional

# Module-level registries. Kept as plain lists so ordering is deterministic
# (registration order) and membership checks stay simple.
_SESSION_START_HOOKS: List[Callable[..., Any]] = []
_SESSION_CLOSE_HOOKS: List[Callable[..., Any]] = []


def register_session_start_hook(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register ``fn`` to be called at session start.

    ``fn`` is invoked as ``fn(session_id, script_path, metadata)``. Registration
    is idempotent (registering the same callable twice is a no-op). Returns
    ``fn`` so it can also be used as a decorator.
    """
    if fn not in _SESSION_START_HOOKS:
        _SESSION_START_HOOKS.append(fn)
    return fn


def register_session_close_hook(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Register ``fn`` to be called at session close.

    ``fn`` is invoked as ``fn(status, exit_code)``, with ``metadata=`` added
    when ``fn`` accepts it (see the module docstring). Registration is
    idempotent.
    Returns ``fn`` so it can also be used as a decorator.
    """
    if fn not in _SESSION_CLOSE_HOOKS:
        _SESSION_CLOSE_HOOKS.append(fn)
    return fn


def unregister_session_start_hook(fn: Callable[..., Any]) -> None:
    """Remove a previously registered start hook (no-op if absent)."""
    try:
        _SESSION_START_HOOKS.remove(fn)
    except ValueError:
        pass


def unregister_session_close_hook(fn: Callable[..., Any]) -> None:
    """Remove a previously registered close hook (no-op if absent)."""
    try:
        _SESSION_CLOSE_HOOKS.remove(fn)
    except ValueError:
        pass


HookFailure = Dict[str, str]
"""``{"phase": "start"|"close", "hook": <qualname>, "error": "Type: msg"}``."""


def _record_failure(
    phase: str, hook: Callable[..., Any], exc: Exception
) -> HookFailure:
    """Build the failure record for a raising hook and WARN about it."""
    # Imported here, not at module scope: this module is imported eagerly by
    # ``import scitex_session`` and must stay import-light.
    from scitex_logging import getLogger

    logger = getLogger(__name__)
    name = getattr(hook, "__qualname__", None) or repr(hook)
    failure = {"phase": phase, "hook": name, "error": f"{type(exc).__name__}: {exc}"}
    logger.warning(
        "scitex-session: session-%s hook %s failed (%s) — provenance for this "
        "session may be incomplete",
        phase,
        name,
        failure["error"],
    )
    return failure


def _fire_session_start_hooks(
    session_id: Any, script_path: Any = None, metadata: Any = None
) -> List[HookFailure]:
    """Fire all registered start hooks; return one record per failed hook.

    A raising hook does not prevent later hooks from running, but its failure
    is logged as a WARNING and returned so the session can record it.
    """
    failures: List[HookFailure] = []
    for hook in list(_SESSION_START_HOOKS):
        try:
            hook(session_id, script_path, metadata)
        except Exception as exc:  # noqa: BLE001 - recorded + warned, not swallowed
            failures.append(_record_failure("start", hook, exc))
    return failures


def _fire_session_close_hooks(
    status: Any = "success",
    exit_code: Any = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> List[HookFailure]:
    """Fire all registered close hooks; return one record per failed hook.

    ``metadata`` is passed as a keyword to every hook that accepts it; a
    hook with the original ``fn(status, exit_code)`` signature is called
    exactly as before.
    """
    failures: List[HookFailure] = []
    for hook in list(_SESSION_CLOSE_HOOKS):
        try:
            if metadata is not None and _accepts_keyword(hook, "metadata"):
                hook(status, exit_code, metadata=metadata)
            else:
                hook(status, exit_code)
        except Exception as exc:  # noqa: BLE001 - recorded + warned, not swallowed
            failures.append(_record_failure("close", hook, exc))
    return failures


def _accepts_keyword(fn: Callable[..., Any], name: str) -> bool:
    """True when ``fn`` can be called with ``name=`` as a keyword."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):  # builtins without a signature
        return False
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        or (
            p.name == name
            and p.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        )
        for p in params
    )


# EOF
