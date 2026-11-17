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
`RandomStateManager`, writes outputs under `script_out/`, and records
clew lineage.

```python
@sess.session
def main(param1="default",
         CONFIG=sess.INJECTED,
         plt=sess.INJECTED,
         logger=sess.INJECTED,
         rngg=sess.INJECTED):
    return 0


if __name__ == "__main__":
    main()
```

Parameters with `sess.INJECTED` as default are filled by the decorator.
Other parameters become CLI flags (`--param1`).

> Do **not** reach for `sess.start` or `sess.run` — they are NOT
> decorators and are easy to misfire (`@sess.start` binds your function
> to the `sys` parameter; `sess.run(name=...)` raises `TypeError` from
> `start()`). They are internal; see the advanced section below. The
> decorator is what you want.

## Provenance capture (automatic)

Nothing to call. At session start the decorator opens the provenance
record (via the session-start hooks a provenance observer such as
scitex-clew subscribes to) **before** it loads `./config/*.yaml`, so every
config file it reads goes through `scitex_io.load` and is recorded as a
run **input**, exactly like a data file you `stx.io.load` yourself.

The start hook also receives re-run metadata (`metadata=`), stored by
clew on the run record and in `CONFIGS/CONFIG.yaml` as `_PROVENANCE`:

| key | content |
|---|---|
| `argv`, `args` | raw `sys.argv` and the parsed CLI arguments |
| `seed` | RNG seed of the session's `RandomStateManager` |
| `cwd` | working directory (relative paths resolve against it) |
| `git` | `root`, `commit`, `branch`, `dirty`, `dirty_paths` — or `{"repository": false}` |
| `packages` | `{distribution: version}` for every imported package |
| `project_root` | the project boundary (see *Project root detection* below) |
| `project_root_source`, `project_root_marker` | which rule chose `project_root` (`argument` / `env` / `git` / `marker` / `cwd` / `script_dir`) and, for `marker`, which marker |
| `local_modules` | `{path: sha256}` of imported modules inside `project_root` |
| `python` | interpreter version and executable |
| `sdir_run`, `sdir_out` | this run's dir and the output base (`<script>_out/`) |
| `started_at`, `notebook_path` | start time; the notebook a Jupyter-kernel session ran in |
| `entry` | how the run was entered; in a kernel `{"kind": "notebook", "path", "path_source", "invocation", "code_sha256", ...}` (see *Notebooks*) |
| `capture_errors` | `{field: error}` for anything that could not be captured |

### Notebooks (Jupyter kernels)

`@session` works when the decorated function is called in a notebook cell
(`main()`), or from a project script the notebook runs with
`%run ../scripts/x.py --n 5`:

- the kernel's own `sys.argv` (`-f <connection file>`) is never parsed —
  the function's defaults apply; under `%run` the script's arguments are;
- the session's script is the **notebook**, resolved from
  `$SCITEX_NOTEBOOK_PATH` > VS Code / Jupyter-server globals
  (`__vsc_ipynb_file__`, `__session__`, `$JPY_SESSION_NAME`) > a `*.ipynb`
  on the launching process's command line (nbconvert / papermill; accepted
  only if it contains the executing cell) > a Jupyter server query. It anchors
  the project root and `<notebook>_out/`. Unresolved: recorded without a code
  file (WARNING; set `$SCITEX_NOTEBOOK_PATH`), outputs under
  `<cwd>/notebook_out/`;
- `entry` records `path`, `path_source`, `invocation` (`cell` | `run` |
  `file`), `code_sha256` (code cells only, scheme `ipynb-code-v1` — stable
  when the notebook is re-executed or saved with outputs), `sha256` (file),
  and the executed-cell history (`executed_cells`, `executed_in_order`;
  unsaved cells are WARNED). A reproducible record is a saved notebook run
  top to bottom (`jupyter nbconvert --to notebook --execute a.ipynb`).

### Project root detection

`project_root` is where "the project" starts: clew records files under it
as project inputs/outputs and everything else as **external**. First match
wins:

1. `@session(project_root="/path")` (or `start(project_root=...)`)
2. `$SCITEX_SESSION_PROJECT_ROOT`
3. the git work-tree root holding the script
4. the nearest ancestor of the script's directory (itself included) with a
   project marker: `.git`, `.hg`, `.svn`, `pyproject.toml`, `setup.cfg`,
   `setup.py`, `scitex/.scitex-structure-version`, or **both `scripts/` and
   `config/`** (the SciTeX research layout) — so a non-git project
   `p/{config,data,scripts}/` gets `p/` as its root
5. the working directory, when the script lies below it
6. the script's own directory — last resort, logged as a WARNING

The upward search stops below `$HOME` (for paths under it) and below `/`,
and does not cross a mount point. An explicit root must be an existing
directory (else `ValueError`). `.scitex/` is not a marker: tools create it
wherever they run (e.g. clew's `<cwd>/.scitex/clew/project-id`).

**Failures are never silent.** A raising hook (or an uncollectable field)
does not break your run, but it is logged as a WARNING naming the hook,
counted, summarised again at close, and recorded in
`CONFIGS/CONFIG.yaml` under `_HOOK_FAILURES` — so an incomplete
provenance record is always visible.

## `sess.INJECTED`

Sentinel sigil for parameters the decorator should fill. Distinct from
`None` so user-supplied `None` is preserved.

## `sess.close(...)`

Finalizer for the lifecycle. The decorator calls it automatically; it is
public for recovery scripts and the manual lifecycle below.

## `sess.SessionManager`

Class form of the lifecycle. Useful for nested or multi-phase runs:

```python
mgr = sess.SessionManager()
mgr.start()
...
mgr.close()
```

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
into `_start`, so unknown kwargs raise `TypeError`.

> The bare `sess.start` / `sess.run` names remain importable for
> backward compatibility but emit a `DeprecationWarning`.

## `sess.running2finished(...)`

Promotes a `script_out/RUNNING/<id>/` directory to
`script_out/FINISHED_SUCCESS/<id>/` (or `FINISHED_FAILURE/`). Called
automatically by `close()`; exposed for recovery scripts.

## See also

- [01_installation.md](01_installation.md)
- [02_quick-start.md](02_quick-start.md)
