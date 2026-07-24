# [stability] Two divergent authentication paths (AuthManager.login vs kompy connector)

**Priority:** Medium &nbsp;·&nbsp; **Labels:** stability, refactor, auth

## Summary

The server has **two independent login mechanisms that don't share state**, which is confusing, wasteful, and a latent source of "authenticated in one place, not the other" bugs.

1. `AuthManager.login()` (`src/komoot_mcp/auth.py`) hits `GET /v006/account/email/{email}/` itself and stores `user_id` / `token`.
2. `KomootClient._get_api()` constructs `kompy.KomootConnector(email, password)`, which logs in *again* independently and stores its own `authentication`.

The `komoot_login` tool exercises path (1). Every actual data/tour call uses path (2). `_basic_auth()` reads from kompy's connector, **not** from `AuthManager`, so `AuthManager.token` / `user_id` are essentially write-only for most flows.

## Evidence

- `komoot_login` → `get_auth_manager().login()` populates `AuthManager.user_id/token` (`tools/auth_tools.py`).
- `KomootClient._basic_auth()` → `api.authentication.get_username()/get_password()` — reads kompy's separate login, ignoring `AuthManager`'s token (`client.py:732`).
- Result: calling `komoot_login` and then a tour tool performs **two** logins; `AuthManager.is_authenticated()` reflects only path (1) and has no bearing on whether tour calls will succeed.

## Impact

- **Wasted round-trips** (see issue 04) and doubled auth-endpoint load.
- **Confusing failure modes:** `komoot_login` can report success while a later call fails to auth (or vice versa), because they authenticate through different code.
- **Maintenance risk:** two places to get credential handling right.

## Proposed fix

Pick one source of truth. Recommended: make `AuthManager` the single authenticator, and have `KomootClient` reuse its token as Basic-auth `(user_id, token)` for the direct-REST helpers, feeding kompy only where kompy's own object model is genuinely required. If kompy must own its login, then `AuthManager.login()`/`is_authenticated()` should delegate to the connector rather than duplicating it, and `komoot_login` should report the connector's state.

## Acceptance criteria

- `komoot_login` success/failure predicts whether subsequent tour tools authenticate.
- At most one login per credential set per request.
- `AuthManager` and the client no longer maintain divergent token copies.
