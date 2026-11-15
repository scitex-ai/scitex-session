---
description: |
  [TOPIC] Public Python API of scitex-session
  [DETAILS] THE public entry point is the `@session` decorator. Also: `close()`, the `SessionManager` class, the `INJECTED` sentinel, `running2finished()`, and the INTERNAL `_start()`/`_run()` low-level entry points.
tags: [scitex-session-python-api]
---

# Python API

```python
import scitex_session as sess
```

## `@sess.session` — THE entry point ⭐

The decorator is the one public, supported way to use scitex-session.
Wrap `def main(...)`; it turns parameters into CLI flags, loads
`config/*.yaml`, configures matplotlib + logging, seeds a
`RandomStateManager`, writes outputs under `script_out/`, and emits lifecycle
events to registered in-process hooks.

Lineage recording requires a subscriber such as Clew to register and handle
those events successfully. Hook dispatch isolates subscriber exceptions so
absent or failing subscribers do not block Session operations. `_hooks.py`
owns registration and dispatch; `tests/scitex_session/test__hooks.py` covers
callback arguments, ordering, empty registries and failure isolation. Persistent
lineage storage and downstream integrations require separate checks.

```python
@sess.session
def main(param1="default",
         CONFIG=sess.INJECTED,
         plt=sess.INJECTED,
         logger=sess.INJECTED,
         rng=sess.INJECTED):
    return 0


if __name__ == "__main__":
    main()
```

Parameters with `sess.INJECTED` as default are filled by the decorator.
Other parameters become CLI flags (`--param1`).

If the body returns but ordinary session cleanup fails, the decorator raises
the cleanup error after final matplotlib cleanup, including calls made inside
an existing caller exception handler. If the body already raised,
that original exception remains primary and the cleanup error is logged.
`_decorator/_run.py::_run_with_session` owns this ordering; real decorated
processes in `tests/integration/test_decorator_cleanup.py` check error/trace
identity, file preservation, callback counts and closed figures.

Both the decorator and advanced runner preserve a body's `SystemExit` object
and traceback. Session exit metadata follows its code: `None` becomes `0`,
an integer is kept, and other values become `1`, following Python SystemExit code conventions.
`tests/integration/test_body_system_exit.py` checks real process statuses,
output routing, records, hooks and primary exits during cleanup collisions.

> Do **not** reach for `sess.start` or `sess.run` — they are NOT
> decorators and are easy to misfire (`@sess.start` binds your function
> to the `sys` parameter; `sess.run(name=...)` raises `TypeError` from
> `start()`). They are internal; see the advanced section below. The
> decorator is what you want.

## `sess.INJECTED`

Sentinel sigil for parameters the decorator should fill. Distinct from
`None` so user-supplied `None` is preserved.

## `sess.close(...)`

Finalizer for the lifecycle. The decorator calls it automatically; it is
public for recovery scripts and the manual lifecycle below.

If ordinary cleanup fails after a session ID is identified, its manager record
is closed with an end time and a nonzero exit code: keep a requested nonzero
code, otherwise use `1`. The original cleanup error still propagates. This
record-only finalization emits no additional hooks; the existing early close
event continues to describe the requested body exit, so it is not a cleanup
success receipt. `_manager.py` / `_lifecycle/_close.py` own this distinction;
`tests/integration/test_decorator_cleanup.py` checks real failed closes,
terminal records, error precedence and unchanged subscriber dispatch.

With no `exit_status`, configuration retains `None` and outputs use neutral
`FINISHED`. Close observers receive `status="success", exit_code=0`, matching
the registry and manager defaults. Explicit nonzero exit codes emit failure
values. `_lifecycle/_close.py` owns this mapping;
`tests/integration/test_manual_close_events.py` checks real manual closes,
observer ordering/values, manager records and retained payloads.

## `sess.SessionManager`

Tracks session records in memory: configuration, timestamps, status and exit
code. `create_session` registers a running record; `close_session` marks it
closed. These operations also fire registered lifecycle hooks. Use the
`@session` decorator for CLI parsing, output-directory setup and log redirection.

```python
import scitex_session as sess

mgr = sess.SessionManager()
mgr.create_session("example", {"seed": 42}, script_path="analysis.py")
active_ids = list(mgr.get_active_sessions())
mgr.close_session("example", status="success", exit_code=0)
record = mgr.get_session("example")  # status="closed", exit_code=0
```

`list_sessions()` retains both running and closed records. The owning API is
`_manager.py`; real manager behavior is checked in
`tests/scitex_session/test__manager.py`. Registered subscribers and full
lifecycle/output behavior require their own integration checks.

## Advanced / INTERNAL: `sess._start(...)` / `sess._run(...)`

These are the low-level building blocks the `@session` decorator
orchestrates. **Prefer the decorator.** They are not part of the default
public surface (absent from `dir(scitex_session)`); reach them via the
underscore aliases only when the decorator genuinely cannot fit.

`sess._start(sys, plt, ...)` — note it takes `sys`/`plt`, it is **not** a
decorator — paired with `sess.close(...)` for a manual lifecycle:

```python
import sys, matplotlib.pyplot as plt
CONFIG, sys.stdout, sys.stderr, plt, COLORS, rng = sess._start(sys, plt)
try:
    ...
finally:
    sess.close(CONFIG)
```

`sess._run(func)` — imperative runner (no decorator, no
`if __name__ == "__main__":` boilerplate). It forwards `**session_kwargs`
unchanged into `_start`; only shared `verbose` (default `True`) goes to
`close`. Accepted startup-only options such as `agg`, `seed` and
`sdir_suffix` no longer become invalid cleanup arguments. Unsupported startup
keywords, including close-only options, still raise `TypeError` before the body.
`tests/integration/test_run_keywords.py` checks real routing, output-directory
effects, early rejection and complete lifecycle/error/file behavior.
Zero-parameter functions are called without arguments, with either the generated
parser or a custom parser. Parameter-bearing functions retain the single
Namespace convention when parsing returns a Namespace-like object; the runner
does not unpack its fields into keyword arguments. Body `TypeError` is propagated
without retrying the body. `tests/integration/test_callable_dispatch.py` checks
these paths, parser rejection before startup and real lifecycle/error handling.
Ordinary cleanup failure is raised if the body returned; if both body and
cleanup fail, the original body exception stays primary and cleanup failure
is logged. `_decorator/_run.py::run` owns this ordering.
`tests/integration/test_imperative_cleanup.py` exercises custom-parser calls,
real file collisions, original error/trace identity and caller-handler context.

> The bare `sess.start` / `sess.run` names remain importable for
> backward compatibility but emit a `DeprecationWarning`.

## `sess.running2finished(...)`

Moves `script_out/RUNNING/<id>/` to `FINISHED_SUCCESS/<id>/` for exit0,
`FINISHED_ERROR/<id>/` for any nonzero exit code, or `FINISHED/<id>/` when
`exit_status=None`. Completion messages use the corresponding success/error/
neutral classification. `close()` calls this helper automatically; recovery
scripts can also call it directly. Real directory and tar.gz payload/logging
checks live in `tests/integration/test_close_exit_status_message.py`.

Copy or requested source-removal errors propagate after logging; completion
messages follow successful requested cleanup. Partial destination output
remains for inspection; no rollback is attempted. `_lifecycle/_close.py` owns
this behavior; `tests/integration/test_finalization_errors.py` checks real
Python/MCP copy collisions and cleanup errors with recoverable bytes intact.

## See also

- [01_installation.md](01_installation.md)
- [02_quick-start.md](02_quick-start.md)
