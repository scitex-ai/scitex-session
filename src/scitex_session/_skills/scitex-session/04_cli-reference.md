---
description: |
  [TOPIC] CLI reference for scitex-session archive helpers
  [DETAILS] `python -m scitex_session.archive {compress,extract}` and the `scitex-session-archive` console-script alias. Both subcommands are dry-run by default; pass `--no-dry-run` (or `--execute`) to write.
tags: [scitex-session-cli-reference]
---

# CLI Reference

`scitex-session` ships one CLI: an archive helper that compresses /
extracts session directories under `script_out/`.

## Invocation

Two equivalent ways to call it:

```bash
# Module form (no install of the console script required)
python -m scitex_session.archive compress ROOT [opts]
python -m scitex_session.archive extract  ROOT [opts]

# Console script — same parser, same opts
scitex-session-archive compress ROOT [opts]
scitex-session-archive extract  ROOT [opts]
```

The console script is installed by `pip install scitex-session` via the
`[project.scripts]` entry in `pyproject.toml`.

## `compress`

Compress every session-shaped directory under `ROOT` into one archive
each. **Dry-run by default** — review the plan before writing.

```bash
scitex-session-archive compress script_out/FINISHED_SUCCESS \
    --older-than-days 30 \
    --format tar.gz \
    --track-bytes \
    --execute
```

| Flag                  | Default     | Meaning                                                 |
|-----------------------|-------------|---------------------------------------------------------|
| `--older-than-days N` | `None`      | Only compress sessions older than `N` days.             |
| `--format FMT`        | `tar.gz`    | One of `tar.gz` / `tar` / `tar.xz`.                     |
| `--pattern STR`       | `None`      | Substring filter on session-dir names.                  |
| `--remove-src` / `--no-remove-src` | `True` | Delete source after archival; `--no-remove-src` retains original files. |
| `--dry-run`           | `True`      | Pass `--no-dry-run` or `--execute` to actually write.   |
| `--max-dirs N`        | `10000`     | Safety cap. `0` disables.                               |
| `--track-bytes`       | `False`     | Compute byte totals; totals stay zero when omitted.    |
| `--use-fd` / `--no-use-fd` | `True` | Use fd when available or force Python enumeration. |
| `--verbose`           | off         | DEBUG-level logging on stderr.                          |

To archive while retaining the original source, add `--no-remove-src`.
The CLI forwards this policy to `archive_existing` in
`_lifecycle/_archive/_bulk.py`;
`tests/scitex_session/test_archive_retention.py` checks real Python, CLI and
in-memory MCP writes across all three formats, including unchanged defaults.

Prints a one-line summary on stdout through shared SciTeX logging.
The payload below omits the logging prefix; diagnostics go to stderr:

```text
compress (executed): scanned=42, candidates=12, archived=12, skipped=0, failed=0, bytes_in=4194304, bytes_out=812345
```

## `extract`

Inverse of `compress`. Extracts every matching archive back into a
session directory.

```bash
scitex-session-archive extract script_out/FINISHED_SUCCESS \
    --pattern '*.tar.gz' \
    --remove-archive \
    --execute
```

| Flag                  | Default       | Meaning                                                 |
|-----------------------|---------------|---------------------------------------------------------|
| `--pattern GLOB`      | `*.tar.gz`    | Glob pattern matched against archive filenames.         |
| `--dest DIR`          | `ROOT`        | Where to write extracted dirs (default: in-place).      |
| `--remove-archive`    | `False`       | Delete archive file after a verified extract.           |
| `--dry-run`           | `True`        | Pass `--no-dry-run` or `--execute` to actually write.   |
| `--max-files N`       | `10000`       | Safety cap. `0` disables.                               |
| `--verbose`           | off           | DEBUG-level logging on stderr.                          |

## Why two invocations?

`python -m scitex_session.archive` works without any console-script
install (handy in CI and in `pip install --no-deps` scenarios). The
`scitex-session-archive` console script is the preferred form for
interactive use — shorter, autocompletes.

Both go through `scitex_session.archive:main`, so behavior is identical.

## See also

- [03_python-api.md](03_python-api.md) — the Python API behind these
  commands (`archive_existing`, `restore_existing`, …).
- [05_mcp-tools.md](05_mcp-tools.md) — same operations exposed as MCP
  tools.
