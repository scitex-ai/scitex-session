---
description: |
  [TOPIC] MCP tools exposed by scitex-session
  [DETAILS] Seven FastMCP tools — four archive helpers, finalize_session, and the §5 skills envelope (skills_list, skills_get). Mounted under the `session_` namespace in the scitex umbrella.
tags: [scitex-session-mcp-tools]
---

# MCP Tools

`scitex-session` ships a single `FastMCP` instance at
`scitex_session._mcp_server.mcp` with seven tools — four archive helpers,
`finalize_session`, and the `§5` skills-discovery envelope. The scitex umbrella mounts the
server under the `session_` namespace via `safe_mount`, so umbrella
clients see the tools as `session_archive_existing` etc.

## Server entry

```bash
# Stdio MCP server (default transport)
scitex-session-mcp
```

`scitex-session-mcp` is registered as a `[project.scripts]` entry in
`pyproject.toml`. The same module is importable for embedding:

```python
from scitex_session._mcp_server import mcp  # FastMCP instance
```

Install the optional dependency to enable the server:

```bash
pip install 'scitex-session[mcp]'
```

## Archive tools (mirror the Python API)

Each tool wraps the same-named function in
`scitex_session._lifecycle._archive` and returns a JSON-encoded string
matching the Python return value.

### `archive_existing`

Compress every session-shaped dir under `root` into a single archive
each. Dry-run by default. The optional `remove_src` defaults to `true`;
set it to `false` to retain the original source directories. The owning bulk
provider and `tests/scitex_session/test_archive_retention.py` verify this
policy through real in-memory MCP requests as well as Python/CLI calls.

```jsonc
// arguments
{
  "root": "script_out/FINISHED_SUCCESS",
  "older_than_days": 30,
  "format": "tar.gz",
  "dry_run": false,
  "track_bytes": true
}
// returns (JSON string)
"{\"scanned\": 42, \"candidates\": 12, \"archived\": 12, \"skipped\": 0, \"failed\": 0, \"bytes_in\": 4194304, \"bytes_out\": 812345}"
```

### `restore_existing`

Inverse of `archive_existing`. Extracts every matching archive under
`root` back into a directory.

```jsonc
{
  "root": "script_out/FINISHED_SUCCESS",
  "pattern": "*.tar.gz",
  "remove_archive": false,
  "dry_run": false
}
```

Returns `{"scanned": N, "candidates": N, "restored": N, "skipped": N, "failed": N}`.

### `archive_session_dir`

Single-directory variant. Compresses `src_dir` into one archive file.

```jsonc
{
  "src_dir": "script_out/FINISHED_SUCCESS/2026-05-24-abc123",
  "format": "tar.gz",
  "remove_src": true
}
```

Returns `{"archive_path": "/.../2026-05-24-abc123.tar.gz"}`.

### `restore_session_archive`

Inverse of `archive_session_dir`. Extracts a single archive file back
into a directory.

```jsonc
{
  "archive_path": "/.../2026-05-24-abc123.tar.gz",
  "dest_dir": null,
  "remove_archive": false
}
```

Returns `{"dest_dir": "/.../2026-05-24-abc123"}`.

## `finalize_session` — recover existing output files

Moves existing `RUNNING/<id>/` files using `running2finished`. Pass
`config` with `SDIR_RUN` pointing at that directory. The JSON-string result
is `{"sdir_run": "<destination>"}`. This is a file recovery operation:
it does not close a SessionManager record or emit session-close hooks.

```jsonc
{"config": {"SDIR_RUN": "script_out/RUNNING/<id>/"}, "exit_status": 0}
```

Exit `0` selects `FINISHED_SUCCESS`, any nonzero code selects
`FINISHED_ERROR`, and omitted/null status selects neutral `FINISHED`.
`remove_src_dir` defaults to `true`; `max_wait` defaults to 60 seconds.
`archive_format: "tar.gz"` optionally returns a destination archive.
This tool has no dry-run option; use it only for files ready to finalize.
Filesystem copy or requested cleanup failures report a tool error. Partial
destination files remain for inspection; there is no automatic rollback.
`tests/integration/test_finalization_errors.py` covers these failure paths.

`_mcp_server.finalize_session` wraps `_lifecycle/_close.running2finished`.
`tests/integration/test_mcp_finalize.py` checks actual MCP discovery,
status/default routing, complete file/archive payloads, invalid requests
and the absence of manager/close-hook effects in local fixtures.
`tests/integration/test_mcp_stdio.py` also checks both standalone stdio
launchers, all seven tools, metadata-owned skill bytes, real files and
error recovery. Hosted/umbrella delivery remains separately unverified.

## Skills envelope (§5 mandate)

Every SciTeX MCP server exposes these two tools so agents can discover
the package's skill pages without filesystem walks.

### `skills_list`

Returns a JSON-encoded mapping from registered skill namespaces to rows.
This package uses the `scitex-session` key. Each row includes `name`,
`owner`, `version`, `path`, `rel_path` and `description`.

```jsonc
// arguments: none
// returns
"{\"scitex-session\": [{\"name\": \"SKILL\", \"description\": \"…\"}, {\"name\": \"01_installation\", \"description\": \"…\"}, …]}"
```

### `skills_get`

Fetches the content of a skill page. `name=None` returns `SKILL.md`.

```jsonc
{
  "name": "04_cli-reference"
}
// returns
"{\"name\": \"04_cli-reference\", \"content\": \"---\\ndescription: …\"}"
```

## Umbrella naming (Convention A)

The standalone source uses **bare names** (`archive_existing`, not
`session_archive_existing`). The scitex umbrella bridge in
`scitex/_mcp_tools/session.py` calls
`safe_mount(mcp, sub_mcp, namespace="session")`, which prefixes every
tool name with `session_` at mount time. Final umbrella-visible names:

| Standalone (this package) | Umbrella                        |
|---------------------------|---------------------------------|
| `archive_existing`        | `session_archive_existing`      |
| `restore_existing`        | `session_restore_existing`      |
| `archive_session_dir`     | `session_archive_session_dir`   |
| `restore_session_archive` | `session_restore_session_archive` |
| `finalize_session`        | `session_finalize_session`      |
| `skills_list`             | `session_skills_list`           |
| `skills_get`              | `session_skills_get`            |

## See also

- [03_python-api.md](03_python-api.md) — the Python functions these
  tools wrap.
- [04_cli-reference.md](04_cli-reference.md) — the equivalent CLI.
