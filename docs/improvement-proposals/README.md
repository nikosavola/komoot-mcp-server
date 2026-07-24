# Improvement Proposals

Project analysis of the Komoot MCP server, focused on **code safety, stability, and performance**. Each file below is written as a self-contained, Markdown-formatted issue that can be pasted directly into GitHub Issues.

> GitHub Issues are currently **disabled** on this repository, so these proposals are tracked as files here instead. Re-enable Issues (Settings → Features → Issues) to promote any of these to a tracked issue.

## Index

| # | Proposal | Priority | Theme |
|---|----------|----------|-------|
| 01 | [Blocking I/O on the event loop (geocoder + routing)](01-blocking-io-event-loop.md) | High | performance / stability |
| 02 | [No CI pipeline; tests fail when real deps are installed](02-no-ci-and-flaky-tests.md) | High | ci / testing |
| 03 | [Timing-unsafe secret comparison in middleware](03-timing-safe-secret-compare.md) | Medium | security |
| 04 | [Fresh login + no connection reuse per request](04-login-per-request-no-session-reuse.md) | Medium | performance |
| 05 | [Two divergent auth paths (AuthManager vs kompy)](05-dual-divergent-auth-paths.md) | Medium | stability / auth |
| 06 | [Directions tool renders raw dicts / wrong data](06-directions-tool-renders-raw-dicts.md) | Medium | correctness |
| 07 | [list_tours silently ignores sort_direction](07-list-tours-ignores-sort-direction.md) | Low | correctness |
| 08 | [Global geocoder shares cross-tenant state](08-geocoder-shared-cross-tenant-state.md) | Medium | stability / concurrency |
| 09 | [Tooling & packaging hygiene](09-tooling-packaging-hygiene.md) | Low | tooling / cleanup |

## Themes at a glance

**Performance & stability (highest leverage).** The server is architected for safe multi-tenancy (per-request `AuthManager`/`KomootClient` via `ContextVars`, an async token-bucket `RateLimiter`, `KomootClient` methods that offload blocking work with `asyncio.to_thread`). Two gaps undercut that design:

- The **geocoder and routing layers block the event loop** (synchronous `requests` / `urllib` / `time.sleep` called inline from `async` tools), so one slow request stalls all concurrent tenants on a worker (01, 08).
- **Every request re-authenticates and opens fresh connections** (04), amplified by two independent login paths (05).

**Safety.** The internal-secret gate — the only barrier between the internet and the MCP endpoint behind the gateway — uses a **non-constant-time comparison** (03).

**Correctness.** Two user-facing tools misbehave silently: directions render raw dicts (06) and `sort_direction` is a no-op (07).

**Process.** There is **no CI**, and the test suite's pass/fail flips depending on whether real dependencies are installed (02); linting, dep pinning, and a few packaging smells round out the hygiene backlog (09).

## Suggested sequencing

1. **02** — stand up CI so every subsequent fix is verified. (Fix the stub determinism as part of this.)
2. **01 + 08** — de-block the event loop; the single biggest stability/throughput win.
3. **03** — quick, high-value security hardening.
4. **04 + 05** — consolidate auth and add connection/token reuse.
5. **06, 07, 09** — correctness and hygiene cleanups.
