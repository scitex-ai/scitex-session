---
description: |
  [TOPIC] Installing scitex-session
  [DETAILS] pip install (standalone vs umbrella) and how to verify the install.
tags: [scitex-session-installation]
---

# Installation

## pip install

```bash
pip install scitex-session
```

Requires Python >= 3.9. Pulls in matplotlib plus the standalone peer
packages `scitex-dict`, `scitex-logging`, `scitex-repro`, and
`scitex-str`.

## Standalone vs umbrella

`scitex-session` is a standalone package, but it is also part of the
[scitex umbrella](https://pypi.org/project/scitex/). The same module
is reachable via two import paths:

```python
# Standalone — pip install scitex-session
import scitex_session as sess

@sess.session
def main(...):
    ...

# Umbrella — pip install scitex
import scitex

@scitex.session.session
def main(...):
    ...
```

`pip install scitex-session` alone does **not** expose the `scitex`
namespace. To get both paths, install both:
`pip install scitex scitex-session` (or `pip install scitex[session]`).

## Verify

```python
import scitex_session
print(scitex_session.__version__)
```

## See also

- [02_quick-start.md](02_quick-start.md) — first `@session` in 30 seconds
- [03_python-api.md](03_python-api.md) — full Python surface
