# [perf/stability] Blocking I/O runs on the event loop in geocoder + routing tools, stalling all concurrent tenants

**Priority:** High &nbsp;·&nbsp; **Labels:** performance, stability, concurrency

## Summary

Several `async def` MCP tools perform **synchronous, blocking network I/O directly on the event loop** instead of offloading it with `asyncio.to_thread`. Under the multi-tenant streamable-HTTP gateway, a single slow geocode or route plan blocks *every other in-flight tenant request* served by that worker — the exact failure mode the `RateLimiter` and `KomootClient` were carefully written to avoid.

The `KomootClient` methods get this right (`await asyncio.to_thread(fn, ...)` everywhere). The geocoder and routing layers do not, and the async tools call them inline.

## Evidence

**1. Geocoder — synchronous `urllib` + `time.sleep`** (`src/komoot_mcp/geocoder.py`):

```python
def _wait(self):
    elapsed = time.monotonic() - self._last_call
    if elapsed < self._min_interval:
        time.sleep(self._min_interval - elapsed)   # blocks the event loop
    ...

def forward(self, query, limit=5):
    self._wait()
    with urllib.request.urlopen(url, timeout=15) as resp:   # blocking socket read
        ...
```

Called directly from `async` tools without `to_thread`:

- `komoot_geocode` → `geocoder.reverse(...)` / `geocoder.forward(...)` (`tools/routing_tools.py:57,68`)
- `komoot_plan_route` → `_parse_location` → `geocoder.forward(...)` (`tools/routing_tools.py:117`)
- `komoot_plan_and_upload` → `_parse_location` → `geocoder.forward(...)` (`tools/routing_tools.py:227`)

The `time.sleep(self._min_interval - ...)` (up to 0.5 s) freezes the whole worker.

**2. Routing — synchronous `requests` on the loop:**

- `komoot_plan_route` calls `routing.plan_route(...)` inline (`tools/routing_tools.py:135`); `RoutingManager.plan_route` issues blocking `self.client.directions(...)` and `requests.post(...)` in `_fetch_gpx` (`routing.py:289,378`).
- `komoot_plan_and_upload` calls `planner.plan(...)` inline (`tools/routing_tools.py:280`); `KomootNativePlanner.plan` issues a blocking `requests.post(..., timeout=60)` (`routing.py:168`) — up to 60 s of frozen event loop.

## Impact

- **Stability:** one tenant's route plan (up to 60 s) or geocode stalls all concurrent tenants on the same worker — head-of-line blocking. Health checks and unrelated tool calls hang.
- **Performance:** throughput collapses to effectively serial under load, defeating the per-request ContextVar isolation the design invested in.

## Proposed fix

1. Wrap the blocking calls in `asyncio.to_thread` at the tool boundary, e.g.:
   ```python
   results = await asyncio.to_thread(geocoder.forward, query, limit)
   result  = await asyncio.to_thread(routing.plan_route, **kwargs)
   route   = await asyncio.to_thread(planner.plan, waypoints=..., sport_komoot=...)
   ```
2. Route the geocoder's rate-limit wait through the async `RateLimiter` (see issue 08) instead of `time.sleep`.
3. Add a regression test asserting no tool coroutine calls a known-blocking symbol synchronously (or a timing test proving concurrency).

## Acceptance criteria

- No `requests.*`, `urllib.request.urlopen`, or `time.sleep` executes on the event-loop thread from any `async` tool.
- Two concurrent `komoot_plan_route` calls overlap in wall-clock time rather than serializing.
