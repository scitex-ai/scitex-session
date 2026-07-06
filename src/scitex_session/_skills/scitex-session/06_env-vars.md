---
name: scitex-session-env-vars
description: Environment variables read by scitex-session at runtime — currently the SCITEX_SESSION_OUT_DIR redirect that points the session `_out` base at node-local scratch for HPC / SLURM-array workloads.
tags: [scitex-session, env-vars]
---

# scitex-session — Environment Variables

Every variable `scitex_session` reads from the environment, per the
ecosystem `SCITEX_<MODULE>_*` naming rule (general skill
`01_ecosystem/04_environment-variables.md`).

| Variable | Purpose | Default | Type |
|---|---|---|---|
| `SCITEX_SESSION_OUT_DIR` | Redirect the whole session `_out` base off the script-adjacent default (see below). | `—` (unset ⇒ script-adjacent) | path |

## `SCITEX_SESSION_OUT_DIR`

By default a session writes its output tree **next to the caller
script**:

```
<script_without_ext>_out/RUNNING/<ID>/
```

Keeping outputs beside the script is the right default for interactive
and small-scale work. But large **SLURM-array / HPC** workloads launch
hundreds of thousands of runs whose per-run session trees live on a
**shared parallel filesystem** (GPFS / Lustre), and each tree is a
directory full of small files — logs, `CONFIGS/`, figures. At that scale
the shared FS runs out of **inodes** long before it runs out of bytes
(operator incident 2026-07-05: neurovista PAC drain hit 100 % inodes /
~7 M files).

Set `SCITEX_SESSION_OUT_DIR` to move the whole `_out` base under a
different directory — typically **node-local scratch**:

```bash
export SCITEX_SESSION_OUT_DIR="$TMPDIR"      # or /tmp, /local/scratch, ...
```

The base then becomes:

```
$SCITEX_SESSION_OUT_DIR/<script_stem>_out/RUNNING/<ID>/
```

so the **entire session lifecycle** — the `RUNNING/` tree, the
running→finished `copytree`, and any `.tar.gz` archive — stays on the
node-local disk and never touches the shared FS.

### Semantics

- **Unset or empty** ⇒ behaviour is byte-for-byte the legacy
  script-adjacent default. This knob is purely additive.
- The redirect keys on the script's **basename stem** (`analyze.py` →
  `analyze_out/`), so scratch dirs stay shallow regardless of how deeply
  nested the script is.
- The trailing **`RUNNING/<ID>/`** segment is preserved, so every
  downstream consumer is unaffected: `setup_configs` still strips it to
  derive `SDIR_OUT`, `running2finished` still moves the run to a
  `FINISHED_*` sibling, and archiving still finds the same tree.
- `<ID>` is unique per run (timestamp to the second + a random suffix),
  so concurrent array tasks never collide even when they share the
  redirected base.

### Caveats

- Node-local scratch is **ephemeral**. When you redirect output there,
  copy back only what you need to keep (the drain persists a sharded
  `.db`, treating the per-run session metadata as disposable). Anything
  left under `$TMPDIR` is lost when the job's node is reclaimed.
- This is an **output-location** knob only. It is distinct from the
  ecosystem-wide `SCITEX_DIR`, which relocates the `~/.scitex/` user-state
  root — session `_out` is research output beside scripts, not user state,
  so it has its own `SCITEX_SESSION_*` variable.

### Reference implementation

`scitex_session._lifecycle._start._resolve_out_base_dir` — the single
place the base is computed (called from `start()`); tests in
`tests/scitex_session/_lifecycle/test__start_out_dir_env.py`.
