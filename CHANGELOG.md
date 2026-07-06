# Changelog

All notable changes to `scitex-session` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
