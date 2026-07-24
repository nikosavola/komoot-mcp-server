# [stability] Global Geocoder singleton shares rate-limit state across tenants and is built non-atomically

**Priority:** Medium &nbsp;·&nbsp; **Labels:** stability, concurrency, multi-tenancy

## Summary

`Geocoder` and `RateLimiter` are process-wide singletons created lazily by `get_geocoder()` / `get_rate_limiter()` with a plain `if _x is None:` check and no lock. Two issues:

1. **Non-atomic lazy init:** under concurrent first-use, two coroutines can both see `None` and construct two instances; whichever assigns last wins and the other is discarded (usually harmless, but it defeats the "single shared limiter" intent and can momentarily under-throttle).
2. **Cross-tenant coupling via the geocoder's own throttle:** `Geocoder` keeps a single `self._last_call` and enforces spacing with `time.sleep`. Because it's a global shared across all tenants, one tenant's geocode delays another's — and it does so by blocking the event loop (see issue 01). This is invisible, shared, mutable state in a system that otherwise went to great lengths (ContextVars) to isolate tenants.

## Evidence

`src/komoot_mcp/context.py`:

```python
_geocoder: Geocoder | None = None

def get_geocoder() -> Geocoder:
    global _geocoder
    if _geocoder is None:        # not atomic
        _geocoder = Geocoder()
    return _geocoder
```

`src/komoot_mcp/geocoder.py` — `self._last_call` + `time.sleep` is shared global state.

## Impact

- Fairness/coupling: tenants interfere with each other's geocode latency through shared throttle state.
- Correctness of throttle: the singleton race can transiently create two limiters, briefly doubling the effective request rate to Komoot/Photon.

## Proposed fix

1. Guard the lazy singletons (module-level init at import, or an `asyncio.Lock`/`threading.Lock` around construction). Given they're cheap, eager module-level construction is simplest.
2. Replace the geocoder's bespoke `time.sleep` throttle with the shared async `RateLimiter` (awaited), so geocode throttling is non-blocking and consistent with Komoot API throttling — this also resolves the geocoder half of issue 01.
3. Document explicitly that these singletons are intentionally tenant-agnostic (they carry no credentials), to distinguish them from the per-request `AuthManager`/`KomootClient`.

## Acceptance criteria

- Geocoder throttling never calls `time.sleep` on the event loop.
- Concurrent first-use cannot create more than one limiter/geocoder.
