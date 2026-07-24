# [stability] Tooling & packaging hygiene: linting, pinned deps, sys.path hack, vestigial config

**Priority:** Low &nbsp;·&nbsp; **Labels:** tooling, packaging, cleanup

## Summary

A cluster of small maintainability/robustness gaps. None is individually urgent, but together they raise the risk of silent regressions and make onboarding harder. Bundled here so they can be triaged as one hygiene pass.

## Items

### 1. No linter / formatter / type checker
No `ruff`, `flake8`, `black`, `mypy`, or pre-commit config exists. Add `ruff` (lint + format) and optionally `mypy` for the client/context layers, wired into CI (issue 02). The codebase already uses `from __future__ import annotations` and type hints in places, so `mypy` would pay off.

### 2. `sys.path` manipulation in `server.py`
```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```
This is a packaging smell — the project is installable (`pip install -e .`), so the package should import via its installed distribution, not a runtime `sys.path` mutation. Remove it and rely on the `src/` layout already declared in `pyproject.toml`.

### 3. Dependency pins are loose / no lockfile
`mcp>=1.0.0` and `openrouteservice>=2.3.0` float freely. `kompy` is correctly pinned `<0.1.0` (relies on internals), but the others can pull breaking majors. Add upper bounds and/or a lockfile (`uv.lock` / `pip-tools`) so CI and prod resolve identically.

### 4. Vestigial `KOMOOT_DATA_DIR`
Documented as vestigial in the README, still set in the `Dockerfile` (`ENV KOMOOT_DATA_DIR=/tmp/komoot`) and its scratch dir created/chowned. If it's truly unused, drop the env var, the `mkdir`, and the chown to shrink the image and reduce confusion. Confirm no code path still reads it first.

### 5. No LICENSE file
README says "This project is provided as-is. See the repository for license details," but there is no `LICENSE` file. Add an explicit license (or a clear statement) so downstream users know their rights.

## Acceptance criteria

- `ruff check` / `ruff format --check` pass in CI.
- `server.py` imports without mutating `sys.path`.
- Runtime deps have upper bounds or a committed lockfile.
- `KOMOOT_DATA_DIR` is either wired to something real or fully removed.
- A `LICENSE` file exists.
