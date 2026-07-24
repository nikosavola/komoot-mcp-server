# [perf] Fresh Komoot login + no HTTP connection reuse on every request

**Priority:** Medium &nbsp;·&nbsp; **Labels:** performance, networking

## Summary

Two compounding inefficiencies make every tenant request more expensive than it needs to be:

1. **A full login round-trip per request.** `KomootClient` is built per-request (ContextVar), and its `_get_api()` lazily constructs `kompy.KomootConnector(email, password)`. `KomootConnector.__init__` performs the login HTTP call. So each request that touches any kompy-backed tool re-authenticates against Komoot from scratch.
2. **No connection pooling.** Every direct-REST helper calls module-level `requests.get/post/request(...)`, which opens a fresh TCP+TLS connection each time. There is no shared `requests.Session`, so keep-alive / connection reuse never happens.

## Evidence

`src/komoot_mcp/client.py`:

```python
def _get_api(self):
    if self._api is None:
        ...
        self._api = kompy.KomootConnector(email, password)   # logs in on construct
    return self._api
```

`_http_get_json`, `_http_request`, `upload_gpx_capture_id`, `save_planned_tour` all call `requests.<verb>(...)` directly — no `Session`.

## Impact

- **Latency:** every tool call pays a login RTT (hundreds of ms) plus a fresh TLS handshake per HTTP call.
- **Load:** under the gateway, N concurrent users = N logins + N handshakes, hammering Komoot's auth endpoint and increasing the chance of hitting rate limits / lockouts.

## Proposed fix

1. **Reuse a `requests.Session`** per `KomootClient` (or per process) so connections keep-alive:
   ```python
   self._session = requests.Session()
   ...
   self._session.request(method, url, ...)
   ```
   The session is per-request-scoped auth data, so it must not be a cross-tenant global carrying credentials — scope it to the `KomootClient` instance (which is per-request) and let the OS/urllib3 pool handle sockets.
2. **Cache the auth token, not the login.** Komoot's v006 login returns a long-lived token (`AuthManager` already models this). Reuse the token across a tenant's requests within a session where feasible, instead of re-logging-in through kompy each time. See issue 05 (dual auth paths) — consolidating auth makes this straightforward.
3. Consider a small, bounded, TTL'd token cache keyed by credential hash for the gateway case, with explicit eviction, if security review approves.

## Acceptance criteria

- A tenant issuing several tool calls in a row triggers at most one login.
- HTTP calls to `api.komoot.de` reuse pooled connections (verifiable via urllib3 connection-reuse logs).
