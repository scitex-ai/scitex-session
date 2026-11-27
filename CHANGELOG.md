# Changelog

All notable changes to `scitex-session` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Configure subprocess coverage via coverage.py `patch = ["subprocess"]`; `tests/conftest.py` no longer writes `.pth` files into site-packages or overwrites `COVERAGE_PROCESS_START`/`COVERAGE_FILE`.

## [0.7.0] - 2026-11-19

### Added

- **Linter: the `@stx.session` rules are now owned and emitted by scitex-session**:
  - structure: `STX-S001`/`S003`/`S004`/`S006`;
  - `STX-S008` (hard-coded analysis parameter; detector ported verbatim);
  - injection: `STX-I006`, `STX-P010`, `STX-P005` (P005 supersedes figrecipe's declaration).

  They moved from the scitex-dev engine, which yields them to this plugin.
  Ids, severities, messages and gates are unchanged, so findings are
  unchanged; this was proven identical with `scitex-dev linter fingerprint`
  on the SciTeX examples corpus. Requires `scitex-dev>=0.65.0` (#96).

## [0.6.1] - 2026-11-19

### Documentation

- **Analysis parameters live in `config/*.yaml`, read from `CONFIG`** (#92).
  The quickstart example, README, skills and the `@session` docstring no
  longer teach numeric `main()` signature defaults or `--threshold 0.7`
  flags. `examples/quickstart.py` reads `CONFIG.QUICKSTART.N_SQUARES` from
  `examples/config/QUICKSTART.yaml`. The docs state that `./config/` is
  resolved against the working directory, keys are upper-cased, there is no
  CLI override for `CONFIG` values, and that `scitex-dev` >= 0.64.0 rule
  STX-S008 flags the old form. No runtime change.

## [0.6.0] - 2026-11-19

### Changed

- **Environment provenance is stored once per environment, not per run**
  (paper-scitex-clew finding: 0.5.x grew a one-session run directory from
  7.8 KB to 36 KB). `CONFIGS/CONFIG.yaml` and `CONFIG.pkl` repeated the full
  `package_provenance` of every imported distribution in every run. The run
  record now keeps `_PROVENANCE.package_provenance_ref`
  (`{format, sha256, distributions, path, abspath}`) and the set itself is
  written once, content-addressed, to
  `<project_root>/.scitex/session/environments/<sha256>.json` (canonical
  JSON; `sha256sum` of the file equals the reference), shared by every run in
  the same environment. Nothing is dropped. If the store cannot be written,
  the record keeps the set inline and says why
  (`package_provenance_store_error`). The metadata handed to observers
  (scitex-clew's run record) is unchanged and still carries the set inline.
  Measured with `examples/measure_run_record_size.py` (3 sessions of a
  load/save script, plain venv): run directory 18 238 -> 7 482 B each
  (`CONFIG.yaml` 10 982 -> 3 521 B, `CONFIG.pkl` 6 380 -> 3 088 B) plus one
  5 682 B environment file: 54 714 -> 28 128 B for the three runs. In the
  paper-scitex-clew solver image (heavier stack, 3 sessions): run directory
  36 722 -> 9 259 B each (`CONFIG.yaml` 23 450 -> 4 520 B, `CONFIG.pkl`
  12 426 -> 3 896 B) plus one 14 122 B environment file: 110 166 -> 41 899 B.

### Added

- `scitex_session.load_run_provenance(run_dir)`: a finished run's
  `_PROVENANCE` with `package_provenance` resolved (hash-checked; looks under
  the record's `project_root`, its `abspath`, then the ancestors of `run_dir`
  for a moved project). Records written inline by 0.5.x read unchanged.
- `examples/measure_run_record_size.py`: run-directory + environment-store
  size of N real sessions under a given interpreter.

## [0.5.1] - 2026-11-18

### Performance

`@session` start + close for a do-nothing script: 3.46 s -> 0.77 s (median of
7, plain venv, scitex-clew stubbed), with the same provenance record. Causes
(found by profiling, paper-scitex-clew E5 finding 4):

- `print_header` slept `sleep(1)` twice per session (2 s). Removed.
- The environment capture and the caller walk read `module.__file__` with
  `getattr` on every `sys.modules` entry (the walk via `inspect.getmodule`),
  which EXECUTED every not-yet-loaded `importlib.util.LazyLoader` module and
  every moved-module alias (`figrecipe._validator` & co.: imports plus a
  `DeprecationWarning` in every run log). Module attributes are now read from
  the module's own `__dict__` without running hooks; a lazy module still
  carries its `__file__` there, so what is recorded is unchanged.
- `inspect.stack()` (source context for every frame) replaced by a plain
  frame walk.
- The distribution index is scanned once per session (reused at close unless
  a `sys.path` directory changed, i.e. something was installed), METADATA
  names are parsed once, and module paths are resolved with a per-directory
  realpath cache.
- The `--help` epilog (figrecipe colour keys, `./config/*.yaml` listing) is
  built only when help is rendered.
- Requires `scitex-str>=0.1.14`, whose import no longer pulls pandas/xarray.
- `package_provenance`: imported distributions are examined on 8 threads
  (per-file stats overlap; 0.44 s -> ~0.15 s on a squashfs/FUSE image).

### Tests

- `tests/scitex_session/_lifecycle/test__environment.py`, `test__caller.py`,
  `tests/scitex_session/_decorator/test__run.py`: CI-robust guards
  (no wall-clock) — no lazy module executed and no `__getattr__` hook called
  by the capture or the caller walk; a do-nothing session imports none of
  pandas/xarray/pyarrow/scipy/playwright/docx/torch and fires no moved-module
  deprecation; a distribution installed mid-run is still attributed at close.

## [0.5.0] - 2026-11-18

Minor release: new run-metadata field `package_provenance` (PR #83), which
scitex-clew >= 0.27.0 reads to flag environment requirements that are not
reproducible from the record.

### Added

- **Install provenance + content fingerprint of imported distributions**
  (run metadata field `package_provenance`). `packages` records
  `{distribution: version}`, which cannot tell a dependency patched and
  rebuilt locally from the index release of the same version: a replay that
  installs the index release then runs other code (seen in a real replay).
  Per imported distribution the record now carries:
  - `source` from PEP 610 `direct_url.json`: `index` (no `direct_url.json`),
    `editable`, `local_dir`, `local_archive`, `remote_archive` or `vcs`,
    with `url` / `commit_id` / `archive_hash`; and `installer`;
  - `fingerprint` (`sha256:`, scheme `record-sha256-v1`) over the
    wheel-shipped `RECORD` entries (`path,hash` lines, sorted), leaving out
    what the installer writes (`.pyc`, `INSTALLER` / `REQUESTED` /
    `direct_url.json` / `RECORD`, `../bin` scripts). Each entry carries the
    hash the wheel builder computed and the installer checked, so the same
    wheel gives the same fingerprint under pip/uv, compiled or not, on any
    machine, and a rebuilt wheel with one changed file differs. One small
    file read per distribution;
  - `modified_files`: installed files whose content no longer matches their
    `RECORD` hash (an index install edited in place keeps version and
    fingerprint). One `stat` per file; a file is hashed only when its size
    differs or it is newer than `RECORD`.
  Each distribution is examined once per run (at start, or at close for one
  imported later). Measured ~1-2 ms per distribution (528 dists: 0.9 s; an
  18k-file distribution ~0.1 s). A failure is recorded per distribution in
  `capture_errors`, never silently dropped.

## [0.4.0] - 2026-11-18

Minor release: a `@session` inside a Jupyter kernel works and records its
notebook (#77, PR #78). New provenance metadata fields (`entry.*` for
notebooks, `io_provenance`) that scitex-clew >= 0.26.0 reads; `script_path`
of a kernel session is now the notebook; requires `scitex-io>=0.5.0`.

### Fixed

- **`@session` works inside a Jupyter kernel (#77).** Three stacked problems:
  1. *Kernel argv parsed as the CLI.* A decorated `main()` called in a cell
     exited with `SystemExit: 2` on the kernel's `-f <connection file>`. In
     IPython, `sys.argv` is now parsed only while `%run` executes a script
     (IPython sets it to the script's arguments); otherwise the function's
     defaults apply, as for a script run without arguments.
  2. *Cell temp file taken as the script.* The caller was
     `/tmp/ipykernel_<pid>/<hash>.py`, so the run directory, the project root
     and the provenance record all landed under `/tmp`. The stack walk now
     recognises IPython cells (a `linecache` name with no file behind it) and
     the `%run` script, and skips installed/stdlib frames.
  3. *Notebook never recorded.* The notebook a kernel executes is resolved —
     `$SCITEX_NOTEBOOK_PATH` > VS Code `__vsc_ipynb_file__` > Jupyter
     `__session__` / `$JPY_SESSION_NAME` > a `*.ipynb` on the launching
     process's command line (nbconvert, papermill, `jupyter execute`; accepted
     only when that notebook contains the cell being executed) > a Jupyter
     server query — and, for an in-cell session, becomes the session's script:
     project root, `<notebook>_out/` and `script_path`. When nothing resolves
     the run is recorded without a code file (`path_unresolved`, WARNING;
     outputs under `<cwd>/notebook_out/`, where scitex-io saves too), never as
     the cell file.
- **A process that outlives its session (a notebook kernel) keeps working.**
  - `close()` now restores the `sys.stdout` / `sys.stderr` and logging
    handlers that `start()` replaced with the run's log tee. Before, the
    closed tee stayed installed. In ipykernel it has no `set_parent`, so the
    output of every later cell (including a second session's) was routed to
    the finished cell and lost.
  - Session ids take their random suffix from the OS entropy source. The
    global `random`, which every session seeds with `seed=42`, gave a second
    session in the same process and second the SAME id and run directory as
    the first.

### Added

- `entry` of a kernel session (`kind: "notebook"`) records `path`,
  `path_source`, `invocation` (`cell` | `run` | `file`), `code_sha256` (code
  cells only — scheme `ipynb-code-v1`, unchanged by re-execution, outputs,
  execution counts or metadata), `sha256` (whole file), `code_cells`, and the
  executed-cell history (`executed_cells`, `executed_in_order`,
  `executed_cells_not_in_file` with a WARNING for unsaved cells).
  `notebook_path` is also set for a `%run` script (its driver notebook).
- The session opens a `scitex_io.provenance_scope` for the run; its
  lost-provenance tally is in the close metadata as `io_provenance`. Loads and
  saves inside a session are no longer reported as "outside any provenance
  scope".

### Changed

- Requires `scitex-io>=0.5.0` (`provenance_scope`). The `dev` extra adds
  `ipykernel`, `nbclient`, `nbconvert`, `nbformat` for the real-kernel tests.
- Caller resolution moved from `_lifecycle/_start.py` to
  `_lifecycle/_caller.py` (re-exported), and the environment collectors
  (`packages` / `unresolved_imports` / `local_modules`) from `_provenance.py`
  to `_lifecycle/_environment.py`. No API change.

### Tests
- Project-root conformance vectors: `tests/scitex_session/conformance/project-root-vectors.json`
  (byte-identical to scitex-clew's `tests/scitex_clew/conformance/` copy, which runs the same table against its
  standalone resolver) is run end to end through `collect_run_metadata`. A
  change to the detection rule must update both implementations and the file
  in both repositories. No code change; no release needed.

## [0.3.7] - 2026-11-18

No scitex-clew change is required: clew's capture already takes `project_root`
from the start metadata. Aligning clew's own project-scope walk is a clew-side
follow-up (see #72).

### Fixed

- **Project root no longer collapses to `scripts/` outside git (#71).** For a
  project that is not a git repository, `project_root` was the entry
  script's directory, so the project's own `data/`, `config/` and sibling
  code were recorded by clew as EXTERNAL inputs. The root is now resolved in
  a documented order: `@session(project_root=...)` >
  `$SCITEX_SESSION_PROJECT_ROOT` > git root > nearest ancestor with a project
  marker (`.git`, `.hg`, `.svn`, `pyproject.toml`, `setup.cfg`, `setup.py`,
  `scitex/.scitex-structure-version`, or `scripts/` + `config/`) > the
  working directory when it contains the script > the script's directory
  (with a WARNING). The search never reaches `$HOME` or `/` and does not
  cross a mount point. The chosen rule is recorded as `project_root_source`
  (and `project_root_marker`). Git projects are unchanged.

## [0.3.6] - 2026-11-17

Pairs with scitex-clew >= 0.23.0, whose `on_session_close(..., metadata=)`
already merges the new close-time payload into the run record. Applying
`relocations` to recorded file paths is a clew-side follow-up (see #67).

### Fixed

- **Third-party imports are recorded (#64).** `packages` now covers every
  distribution behind an imported top-level module — also modules imported
  inside the run (re-collected at close), editable installs (PEP 610
  `direct_url.json`) and distributions without `top_level.txt` — and is
  only attributed when the distribution's files are where the module was
  loaded from. Imported code no distribution owns is listed in the new
  `unresolved_imports` instead of being dropped. The scan reads metadata
  text only (tens of ms instead of `packages_distributions()`' seconds).
- **`python -c` / stdin / REPL sessions are not attributed to a file (#65).**
  Such a session records `script_path = None` and a new `entry` field
  (`{"kind": "command", "command_sha256", "command"}` for `-c`); the caller
  walk never falls back to a scitex_session-internal file. The project root
  is the working directory (git root when inside a repository) and outputs
  go to `<cwd>/python_c_out/` (`python_stdin_out/`, `python_interactive_out/`).
- **Run-dir outputs are handed over at their final path (#66).** `close()`
  now moves `RUNNING/<ID>` to `FINISHED_*/<ID>` (a single rename when
  possible, so the tee'd logs keep their writes) BEFORE firing the close
  hooks, and passes `metadata={"relocations": [{"from", "to"}],
  "sdir_run_final", "packages", "unresolved_imports", "local_modules",
  "close_capture_errors"}` to every close hook that accepts a `metadata`
  keyword (two-argument hooks are called as before). Covers success, error
  and other exit statuses. `CONFIGS/CONFIG.yaml` is written in the final
  directory and carries the same fields under `_PROVENANCE`.
- **Dependency: `scitex-io>=0.4.2`.** Older scitex-io wrote unparseable
  YAML for any list of mappings (e.g. `_HOOK_FAILURES`, `relocations`) in
  `CONFIGS/CONFIG.yaml` (fixed in scitex-io 0.4.2, `cfbc381`). The ANSI
  cleanup of the run's logs now finds them (the glob lacked a separator).

## [0.3.5] - 2026-11-17

Pairs with scitex-clew 0.23.0 (audit-hook capture), which reads
`project_root`; clew derives the same boundary itself when it is absent,
so neither package requires the other's new version.

### Added

- **`project_root` in the start-hook metadata.** The boundary that
  `local_modules` already used (git root, else the script's directory) is now
  sent explicitly, so a provenance observer that captures file access and
  imported code (scitex-clew's audit-hook capture) uses the same definition of
  "project-local" as the session. `None` when no boundary is known.

## [0.3.4] - 2026-11-16

First published release of the 0.3.3 changes below (the `v0.3.3` tag was
cut but never reached PyPI; its release build could not be admitted to the
SIF runner pool). No library code changes relative to 0.3.3.

### Fixed

- Release workflow: use the same runner-admission revision as the pinned
  test matrix (fixed trusted-actor ID allowlist). The previous revision
  depended on an unauthenticated GitHub API call from a hosted runner and,
  when it failed, routed the build to `ubuntu-latest`, which has no
  apptainer, so the verified-SIF build stopped (observed on both v0.3.3
  publication attempts).

## [0.3.3] - 2026-11-16

### Added

- **Config files are recorded as run inputs.** The session-start hooks now fire
  *before* `./config/*.yaml` is loaded, so every config file read through
  `scitex_io.load` is recorded by a provenance observer (scitex-clew) as an
  input of the run, like any data file.
- **Re-run metadata on the start hook.** `metadata=` now carries `argv`, parsed
  `args`, `seed`, `cwd`, `python`, `git` (commit/branch/dirty), imported
  `packages` versions, `local_modules` sha256 hashes, `sdir_run`/`sdir_out`,
  `started_at` and `capture_errors` for every session (previously only
  `notebook_path`, only for notebooks). Also stored as `_PROVENANCE` in
  `CONFIGS/CONFIG.yaml`.

### Fixed

- **Hook failures are no longer silent.** A raising start/close hook is still
  isolated, but is logged as a WARNING, returned by the registry, summarised
  at close and recorded as `_HOOK_FAILURES` in `CONFIGS/CONFIG.yaml`.
- Start/close hooks fired twice per session (lifecycle + `SessionManager`); the
  second start event carried no script path or metadata and overwrote the
  observer's run record. `SessionManager.create_session/close_session` take
  `fire_hooks=` and the lifecycle fires each event exactly once.
- A config-loading error after the run record was opened now closes it as
  `failed` instead of leaving it `running`.
- `close()` dropped every private CONFIG key, so the tee'd log streams were
  never closed; they now are.
- README taught the deprecated `scitex.INJECTED`; now `scitex.session.INJECTED`
  (and `rngg`, the actual injected name).

## [0.3.2] - 2026-11-12

- Include the reviewed HTML reference in wheel and source distributions, and verify every bundled document against its source during release builds.

- Run release validation through the shared full test matrix, verify the CI image, use job-owned portable build scratch, and publish only the exact tested source.

- Defer lifecycle initialization so archive imports and CLI discovery do not load plotting or scientific data libraries.

- Declare the shared development and logging APIs used by the session lifecycle as runtime dependencies.
- Route lifecycle diagnostics and archive CLI summaries through shared logging while retaining summaries on stdout.
- Reuse pinned shared CI workflows while retaining both protected branches’ required test contexts, strict PR docs and reviewed documentation bundle updates.
- Validate nested peer imports after checking the optional root package; align lazy-import regression tests with their source module.

- Remove the legacy admin auto-merge workflow and require the full declared test profile without fallback installs.

- Render the session decorator reference without an unsupported nested section heading.

### Added

- **`SCITEX_SESSION_OUT_DIR` — redirect the session `_out` base off the
  shared filesystem.** When set (and non-empty), the whole session output
  base moves from the script-adjacent default
  (`<script>_out/RUNNING/<ID>/`) to
  `$SCITEX_SESSION_OUT_DIR/<script_stem>_out/RUNNING/<ID>/`. This lets
  HPC / SLURM-array workloads point session output at node-local scratch
  (e.g. `$TMPDIR`) so the entire lifecycle — the RUNNING tree, the
  running→finished copytree, and any archive — stays off a shared parallel
  filesystem (GPFS/Lustre) whose inode budget the per-run session trees
  would otherwise exhaust. Default (unset) behaviour is byte-for-byte
  unchanged. The trailing `RUNNING/<ID>/` segment is preserved, so
  `SDIR_OUT` derivation, `running2finished`, and archiving are unaffected.
  Documented in the package env-vars skill leaf
  (`_skills/scitex-session/06_env-vars.md`). Driver: operator inode-exhaustion
  incident 2026-07-05 (neurovista PAC drain).

### Changed

- **API hygiene — `start` / `run` demoted to internal.** The
  `@scitex_session.session` decorator is now the single prominent public
  entry point. The low-level `start()` (`start(sys, plt, ...)`, not a
  decorator) and the imperative `run()` are removed from `__all__` and
  from `dir(scitex_session)` so a scanning agent can no longer misfire on
  them (e.g. `@scitex_session.start` bound `main` to `sys`; `run(name=...)`
  raised `TypeError` from `start()`). Power users keep access via the new
  underscore aliases `scitex_session._start` / `scitex_session._run`.

### Deprecated

- Bare `scitex_session.start` / `scitex_session.run` remain importable for
  backward compatibility but emit a `DeprecationWarning` on access;
  use the `@scitex_session.session` decorator (or `_start` / `_run` for
  low-level access) instead.

## [0.2.0] — 2026-05-26

### Added

- **Bidirectional archive helpers** (`scitex_session.archive_session_dir`,
  `restore_session_archive`, `archive_existing`, `restore_existing`)
  for compressing scitex session directories into a single `.tar.gz`
  (1 inode vs 7 per session, ~85% inode reduction on flat hotspots).
- **`@session(archive_format="tar.gz")` opt-in** — finished sessions
  collapse to a single archive file in `FINISHED_SUCCESS/<session>.tar.gz`
  instead of a directory. Default `archive_format=None` preserves the
  copytree behaviour bit-for-bit.
- **MCP server** (`scitex_session._mcp_server.mcp`, FastMCP-based) with 6
  tools: `archive_compress`, `archive_extract`, `archive_single`,
  `restore_single`, `finalize_session`, plus the §5 `skills_list` /
  `skills_get` envelope.
- **CLI**: `python -m scitex_session.archive {compress,extract} <root>`
  plus the `scitex-session-archive` and `scitex-session-mcp` console
  scripts.
- **`fastmcp` optional dep** under the `mcp` extra.
- **Skills**: `04_cli-reference.md`, `05_mcp-tools.md` added under
  `_skills/scitex-session/`. SKILL.md interface ratings bumped
  (cli 1 → 2, mcp 0 → 2).
- **fd / ripgrep conditional fast-path** (`HAS_FD`, `HAS_RG` detection)
  for candidate enumeration; transparent fallback to the Python
  `iterdir + stat` path when those tools aren't installed.
- **`track_bytes: bool = False`** opt-out on bulk archive ops to skip
  the per-session `dir_size()` os.walk pass that dominated the
  2026-05-25 neurovista 80,408-session reclaim run.

### Changed

- Refactor `src/scitex_session/_decorator.py` (648 lines) into a
  4-file sub-package (`_decorator/{__init__,_decorator,_run,_cli}.py`,
  all ≤ 512 lines). All existing `from scitex_session._decorator
  import session, run` imports continue to resolve via the new
  `__init__.py` re-exports.
- Rewrite `_decorator.session` docstring to NumPy-style sections +
  `.. code-block:: python` blocks — fixes 3 sphinx "Unexpected
  indentation" RST errors on PR builds with `-W`.

### Fixed

- audit-cli / audit-mcp-tools / audit-skills / audit-python-apis /
  audit-project all green under scitex-dev v0.12.2 (refactored test
  layout for PS-202 / PS-204 / PS-205, added `src/scitex_session/__main__.py`
  for PS-105).
- Sphinx-build `-W` exits 0 (autosummary turned off, suppress flags
  for duplicate-object descriptions, index.rst rewritten).

## [0.1.0]

- Initial CHANGELOG entry — see git log for prior history.
