# scitex-session

<p align="center">
  <a href="https://scitex.ai">
    <img src="docs/scitex-logo-blue-cropped.png" alt="SciTeX" width="400">
  </a>
</p>

<p align="center"><b>`@session` decorator + lifecycle (auto-CLI, output dir tree, randomstate, configs).</b></p>

<p align="center">
  <a href="https://scitex-session.readthedocs.io/">Full Documentation</a> · <code>uv pip install scitex-session[all]</code>
</p>

<!-- scitex-badges:start -->
<p align="center">
  <a href="https://pypi.org/project/scitex-session/"><img src="https://img.shields.io/pypi/v/scitex-session?label=pypi" alt="pypi"></a>
  <a href="https://pypi.org/project/scitex-session/"><img src="https://img.shields.io/pypi/pyversions/scitex-session?label=python" alt="python"></a>
  <a href="https://scitex-session.readthedocs.io/en/latest/"><img src="https://img.shields.io/readthedocs/scitex-session?label=docs" alt="docs"></a>
</p>
<p align="center">
  <a href="https://github.com/scitex-ai/scitex-session/actions/workflows/pytest-matrix-on-ubuntu-py3-11-3-12-3-13.yml"><img src="https://img.shields.io/github/actions/workflow/status/scitex-ai/scitex-session/pytest-matrix-on-ubuntu-py3-11-3-12-3-13.yml?branch=develop&label=tests" alt="tests"></a>
  <a href="https://github.com/scitex-ai/scitex-session/actions/workflows/import-smoke-on-ubuntu-py3-12.yml"><img src="https://img.shields.io/github/actions/workflow/status/scitex-ai/scitex-session/import-smoke-on-ubuntu-py3-12.yml?branch=develop&label=install-check" alt="install-check"></a>
  <a href="https://github.com/scitex-ai/scitex-session/actions/workflows/scitex-session-quality-audit-on-ubuntu-latest.yml"><img src="https://img.shields.io/github/actions/workflow/status/scitex-ai/scitex-session/scitex-session-quality-audit-on-ubuntu-latest.yml?branch=develop&label=quality" alt="quality"></a>
  <a href="https://codecov.io/gh/ywatanabe1989/scitex-session"><img src="https://img.shields.io/codecov/c/github/ywatanabe1989/scitex-session/develop?label=cov" alt="cov"></a>
</p>
<!-- scitex-badges:end -->

---

## Quick Start

```python
import scitex_session as sess

@sess.session
def main(
    param1="default",
    CONFIG=sess.INJECTED,
    plt=sess.INJECTED,
    logger=sess.INJECTED,
    rng=sess.INJECTED,
):
    """Docstring becomes --help."""
    logger.info("hi")
    return 0
```

## Demo

```mermaid
sequenceDiagram
    participant User as $ python script.py --param1 X
    participant Dec as @sess.session
    participant Main as main()
    participant FS as Filesystem
    User->>Dec: invoke
    Dec->>Dec: parse args, fix RNG, build CONFIG
    Dec->>FS: mkdir script_out/RUNNING_<ID>/
    Dec->>Main: call(CONFIG, logger, rng, plt)
    Main->>FS: stx.io.save(...)
    Main-->>Dec: return 0
    Dec->>FS: rename → FINISHED_SUCCESS/<ID>/
    Dec->>FS: write CONFIGS/CONFIG.yaml + logs/
```

<p align="center"><sub><b>Figure 1.</b> Session lifecycle: the decorator parses args, runs <code>main()</code> under a RUNNING directory, then promotes it to FINISHED_SUCCESS with configs and logs.</sub></p>

## Installation

```bash
uv pip install "scitex-session[all]"
```

Requires Python ≥ 3.9.

<details>
<summary><b>Per-extra installs</b></summary>

<br>

| Extra | Pulls in |
|---|---|
| `mcp` | stdio MCP server (`fastmcp`) |
| `dev` | tests + lint + dev helpers (`pytest`, `ruff`, `scitex-dev`, `figrecipe`, …) |
| `docs` | Sphinx docs build (`sphinx`, `myst-parser`, …) |

</details>

## Architecture

```
src/scitex_session/
├── __init__.py        # public re-exports (session, INJECTED, start, close, ...)
├── _decorator.py      # @session — auto-CLI + DI + output-dir lifecycle
├── _manager.py        # SessionManager (class-style alternative)
├── _lifecycle/        # start / close / FINISHED_SUCCESS dir tree
└── template.py        # boilerplate template for new scripts
```

```mermaid
flowchart LR
    fn["@sess.session\ndef main(...)"] --> cli["auto-CLI\n(argparse from signature)"]
    cli --> inject["DI: CONFIG / logger / plt / rng"]
    cfg["./config/*.yaml"] --> inject
    inject --> body["main() body"]
    body --> save["stx.io.save"]
    save --> rundir[("script_out/FINISHED_SUCCESS/<ID>/")]
    rundir --> cfgsnap[("CONFIGS/CONFIG.yaml")]
    rundir --> logs[("logs/{stdout,stderr}.log")]
    rundir --> outputs[("results.csv, plots, ...")]
```

<p align="center"><sub><b>Figure 2.</b> Decorator data flow: signature-derived CLI, dependency injection, and the output directory tree.</sub></p>

## 1 Interfaces

<details open>
<summary><strong>Python API</strong></summary>

<br>

```python
import scitex_session as sess

# Decorator — wraps main() with auto-CLI, output dir, configs, RNG.
@sess.session
def main(CONFIG=sess.INJECTED, logger=sess.INJECTED, rng=sess.INJECTED):
    ...

# Manual lifecycle (advanced / internal — prefer the decorator above).
# `_start` is the low-level entry point: `_start(sys, plt, ...)`, NOT a
# decorator. The bare `sess.start` name is deprecated.
sess._start(sys, plt, ...)
sess.close(CONFIG)

# Class-style manager
mgr = sess.SessionManager()
```

</details>

## Status

Standalone fork of `scitex.session`. Deps: matplotlib + scitex-dict /
-logging / -repro / -str (already-standalone peer packages).

Decoupling notes:
- `scitex.dict.DotDict` → `scitex_dict.DotDict`
- `scitex.repro.RandomStateManager / gen_ID` → `scitex_repro.*`
- `scitex.str.clean_path / printc` → `scitex_str.*`
- `scitex.logging.getLogger` → `scitex_logging.getLogger` (PS-220)
- `scitex.plt.utils._configure_mpl` → optional via `try/except` with no-op
  fallback (matplotlib uses defaults if scitex umbrella isn't installed).
- `scitex.utils._notify` → optional via `try/except` (silent no-op fallback).

The umbrella package's `scitex.session` import path is preserved via a
`sys.modules`-alias bridge.

## Part of SciTeX

`scitex-session` is part of [**SciTeX**](https://scitex.ai). Install via
the umbrella with `pip install scitex[session]` to use as
`scitex.session` (Python) or `scitex session ...` (CLI).

>Four Freedoms for Research
>
>0. The freedom to **run** your research anywhere — your machine, your terms.
>1. The freedom to **study** how every step works — from raw data to final manuscript.
>2. The freedom to **redistribute** your workflows, not just your papers.
>3. The freedom to **modify** any module and share improvements with the community.
>
>AGPL-3.0 — because we believe research infrastructure deserves the same freedoms as the software it runs on.

## License

AGPL-3.0-only (see [LICENSE](./LICENSE)).

---

<p align="center">
  <a href="https://scitex.ai" target="_blank"><img src="docs/scitex-icon-navy-inverted.png" alt="SciTeX" width="40"/></a>
</p>
