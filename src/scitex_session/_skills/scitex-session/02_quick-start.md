---
description: |
  [TOPIC] First `@session` in 30 seconds
  [DETAILS] Wrap `main()` with the decorator; the lifecycle handles CLI parsing, config loading, matplotlib + logging setup, and writes outputs to `script_out/<status>/<session_id>/`.
tags: [scitex-session-quick-start]
---

# Quick Start

## The decorator

```yaml
# config/ANALYSIS.yaml  ->  CONFIG.ANALYSIS.THRESHOLD
THRESHOLD: 0.5
```

```python
import scitex_session as sess

@sess.session
def main(
    data_path="data.csv",      # Auto-CLI: --data-path (an input, not a parameter)
    CONFIG=sess.INJECTED,      # Loaded from ./config/*.yaml
    plt=sess.INJECTED,         # Pre-configured matplotlib
    logger=sess.INJECTED,      # Session logger
    rng=sess.INJECTED,         # Reproducible RandomStateManager
):
    """Docstring becomes --help."""
    threshold = CONFIG.ANALYSIS.THRESHOLD
    logger.info(f"{data_path}: threshold={threshold}")
    return 0


if __name__ == "__main__":
    main()
```

Run it from the directory that holds `config/`:

```bash
python my_script.py --data-path subject01.csv
python my_script.py --help   # CLI flags + the CONFIG values found
```

## Where parameters live

Every analysis parameter (threshold, window size, sample count, seed,
frequency band) goes in `config/*.yaml` and is read from `CONFIG`; never
write it as a numeric `main()` default or a `--threshold 0.5`-style flag.
Keep CLI arguments for inputs such as paths or subject ids.

- `./config/` is resolved against the **working directory** (not the
  script's directory and not the project root).
- The file stem is the namespace and every key is upper-cased at load
  time: `config/ANALYSIS.yaml` with `threshold: 0.5` is
  `CONFIG.ANALYSIS.THRESHOLD`.
- There is no CLI override for `CONFIG` values. Edit the YAML; each run
  freezes the values it used in `CONFIGS/CONFIG.yaml`.
- `scitex-dev` >= 0.64.0 lint rule **STX-S008** flags numeric signature
  defaults, numeric keyword arguments, number-valued assignments, float
  thresholds and seeds (a WARNING under `project-type: research`).

## What the decorator does

1. Parses `def main(...)` parameters as CLI flags (inputs only).
2. Loads `./config/*.yaml` into `CONFIG` (a `DotDict`): the parameters.
3. Configures matplotlib (`plt`) and stdlib `logging` (`logger`).
4. Seeds a reproducible `RandomStateManager` (`rng`).
5. Runs the wrapped function.
6. Writes outputs under `script_out/FINISHED_SUCCESS/<session_id>/`
   (or `FINISHED_FAILURE/` on exception).

## Manual lifecycle (advanced / internal)

The `@session` decorator is THE entry point — use it. Only if its
contract genuinely doesn't fit, drive the lifecycle yourself with the
**internal** underscore alias `sess._start` (it is `_start(sys, plt, ...)`,
not a decorator):

```python
import sys, matplotlib.pyplot as plt
import scitex_session as sess

CONFIG, sys.stdout, sys.stderr, plt, COLORS, rng = sess._start(sys, plt)
try:
    ...
finally:
    sess.close(CONFIG)
```

## See also

- [03_python-api.md](03_python-api.md) — full Python surface
