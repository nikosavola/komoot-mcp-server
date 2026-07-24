# [stability/ci] No CI pipeline, and the test suite fails when real dependencies are installed

**Priority:** High &nbsp;·&nbsp; **Labels:** ci, testing, stability

## Summary

There is **no CI** (`.github/workflows/` does not exist), so the test suite never runs automatically on push/PR. Worse, the suite is **environment-dependent**: it passes only when `kompy` / `openrouteservice` are *absent* (and the conftest stubs kick in), and **fails when the real packages are installed**.

## Evidence

`tests/conftest.py` installs stubs only "if missing":

```python
def _install_kompy_stub_if_missing():
    try:
        import kompy  # noqa: F401
        return          # real kompy present -> stub NOT installed -> tests assume stub shape
    except ModuleNotFoundError:
        pass
```

Running the suite in a venv with the real deps installed (`kompy==0.0.12`, `openrouteservice==2.3.3`):

```
7 failed, 130 passed
FAILED tests/test_ors_context.py::...::test_uses_contextvar_key
  AttributeError: 'Client' object has no attribute 'key'      # test expects the STUB client
FAILED tests/test_tools_smoke.py::...::test_list_tours_tool_renders_string
  AssertionError: 'tour-for-carol@x.com' in 'Error listing tours: Connection to Komoot API failed...'
...
```

The failures are not real regressions — they are tests asserting against stub internals (`mgr.client.key`, the stubbed connector) that only exist when the real libraries are missing. Consequences:

- **The pass/fail signal depends on what happens to be installed**, which is exactly what CI should make deterministic.
- A contributor with the real packages installed sees red locally and can't distinguish real breakage from environment drift.

## Proposed fix

1. **Add a CI workflow** (`.github/workflows/ci.yml`) running on push + PR:
   - matrix over Python 3.11 / 3.12,
   - `pip install -e ".[dev]"`,
   - `pytest`,
   - a lint/format gate (see issue 09).
2. **Make the suite deterministic regardless of installed deps.** Either:
   - force the stubs on unconditionally in tests (inject the fake `openrouteservice.Client` / kompy connector rather than only-if-import-fails), or
   - split into unit tests (always stubbed) + an opt-in integration job (real deps, real creds via secrets, `-m integration`).
3. Pick the canonical target and pin it: CI either installs the real deps (then the stub-only tests must be fixed) or runs without them (then document that and gate the stub path).

## Acceptance criteria

- `pytest` produces the same result whether or not `kompy`/`openrouteservice` are installed.
- A green CI check is required before merge.
