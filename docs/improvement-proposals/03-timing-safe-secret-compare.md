# [security] Timing-unsafe comparison of INTERNAL_SECRET / GATEWAY_SECRET

**Priority:** Medium &nbsp;·&nbsp; **Labels:** security, hardening

## Summary

`InternalSecretMiddleware` authenticates every non-health request by comparing the caller-supplied `Authorization` header against the expected secret with Python's `==`. String `==` short-circuits on the first differing byte, so its run time leaks how many leading bytes matched. This is a classic side-channel that lets an attacker recover the secret byte-by-byte over many requests.

## Evidence

`src/komoot_mcp/middleware.py`:

```python
expected_direct  = f"{BEARER_PREFIX}{secret}"
expected_gateway = f"{BEARER_PREFIX}Internal-gateway:{gateway_secret}" if gateway_secret else None

if provided == expected_direct:            # <-- non-constant-time
    pass
elif expected_gateway is not None and provided == expected_gateway:   # <-- non-constant-time
    pass
else:
    # 401
```

This header is the *only* thing standing between the public internet and the MCP endpoint when the server is exposed behind the gateway, so it is a high-value target.

## Proposed fix

Use `hmac.compare_digest`, which runs in time independent of where the mismatch occurs:

```python
import hmac

def _matches(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)

ok = _matches(provided, expected_direct) or (
    expected_gateway is not None and _matches(provided, expected_gateway)
)
if not ok:
    # 401
```

Notes:
- Keep the existing behavior of reading the env vars at request time (rotation without restart).
- `compare_digest` on differing-length inputs is still safe; no need to pre-check length.

## Acceptance criteria

- Secret comparison uses `hmac.compare_digest`.
- Unit test covers accept (direct + gateway forms) and reject paths.
