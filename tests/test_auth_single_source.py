"""One authenticator, one login, one token (issue 05).

Before this, ``AuthManager.login()`` did its own ``GET /v006/account/
email/{email}/`` while ``KomootClient`` separately built a
``kompy.KomootConnector`` that logged in again. The ``komoot_login`` tool
drove the first path; every tour/data call used the second. These tests
pin the properties that made that split a bug:

* ``komoot_login`` success means later client calls authenticate, and
  ``komoot_login`` failure means they don't.
* A credential set logs in **at most once** per request, no matter how
  many kompy-object and direct-REST calls follow.
* ``_basic_auth()`` hands ``requests`` the ``(user_id, token)`` pair —
  never the account password.
* Two ContextVar scopes never share a token or a connector.

The real ``kompy`` may or may not be installed (``tests/conftest.py``
stubs it when it isn't), so every test here patches
``kompy.KomootConnector`` with a spy. ``komoot_mcp.auth`` calls it as a
module attribute precisely so this works.
"""
from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace
from unittest.mock import patch

import kompy
import pytest

from komoot_mcp.auth import AuthError, AuthManager
from komoot_mcp.client import KomootAPIError, KomootClient
from komoot_mcp.context import (
    clear_request_state,
    get_client,
    reset_auth_manager,
    set_auth_manager,
)


@pytest.fixture(autouse=True)
def _reset():
    clear_request_state()
    yield
    clear_request_state()


class _NoLimit:
    async def acquire(self):
        return None


class _SpyConnector:
    """Stand-in for ``kompy.KomootConnector`` that counts logins.

    ``KomootConnector.__init__`` *is* the login in kompy, so counting
    constructions counts logins. Instances are recorded on the class-
    level ``calls`` list by the factory below.
    """

    def __init__(self, email, password, calls):
        calls.append(email)
        self.email = email
        self.password = password
        # Mirror kompy's post-login ``Authentication``: the numeric user
        # id under ``get_username``, the long-lived token under
        # ``get_token``, and the *account password* (deliberately a
        # different value) under ``get_password``.
        self.authentication = SimpleNamespace(
            get_username=lambda: f"uid-{email}",
            get_token=lambda: f"token-{email}",
            get_password=lambda: password,
            get_email_address=lambda: email,
        )

    def get_tours(self, **kwargs):
        return [SimpleNamespace(id=1, name=f"tour-for-{self.email}")]


def _spy(fail_with=None):
    """Return ``(patcher, calls)`` for a counting connector factory."""
    calls: list[str] = []

    def factory(email, password):
        if fail_with is not None:
            calls.append(email)
            raise fail_with
        return _SpyConnector(email, password, calls)

    return patch.object(kompy, "KomootConnector", factory), calls


def _register(module_name):
    """Register a tools module against a minimal FastMCP recorder."""
    registered: dict[str, callable] = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    from importlib import import_module

    import_module(f"komoot_mcp.tools.{module_name}").register(_Mcp())
    return registered


def _resp(status=200, json_body=None, text=""):
    def _json():
        if json_body is None:
            raise ValueError("no json")
        return json_body

    return SimpleNamespace(
        status_code=status, text=text, json=_json, ok=200 <= status < 300,
    )


class TestLoginPredictsSubsequentCalls:
    @pytest.mark.asyncio
    async def test_login_success_means_client_calls_authenticate(self):
        """``komoot_login`` OK ⇒ the very same session serves tour calls."""
        auth_tools = _register("auth_tools")
        patcher, calls = _spy()

        am = AuthManager(email="alice@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            with patcher:
                out = await auth_tools["komoot_login"]()
                assert "Successfully authenticated as user uid-alice@x.com" == out

                # The client must now be able to authenticate *without*
                # logging in again, and must be holding the identity the
                # login tool just reported.
                client = KomootClient(am, _NoLimit())
                assert client._basic_auth() == (
                    "uid-alice@x.com", "token-alice@x.com",
                )
                tours = await client.list_tours()
                assert tours["tours"][0]["name"] == "tour-for-alice@x.com"
        finally:
            reset_auth_manager(token)

        assert calls == ["alice@x.com"], "login must happen exactly once"

    @pytest.mark.asyncio
    async def test_login_failure_means_client_calls_do_not_authenticate(self):
        """``komoot_login`` failing ⇒ subsequent calls fail too, not succeed.

        This is the regression the old split allowed: the login tool and
        the tour calls authenticated through different code, so one could
        report success while the other failed.
        """
        auth_tools = _register("auth_tools")
        patcher, calls = _spy(
            fail_with=ConnectionError(
                "Connection to Komoot API failed. Please check your credentials."
            )
        )

        am = AuthManager(email="bob@x.com", password="wrong")
        token = set_auth_manager(am)
        try:
            with patcher:
                out = await auth_tools["komoot_login"]()
                assert out.startswith("Login failed: ")
                assert "check your credentials" in out.lower()
                assert not am.is_authenticated()

                client = KomootClient(am, _NoLimit())
                with pytest.raises(KomootAPIError):
                    await client.list_tours()
                with pytest.raises(KomootAPIError):
                    client._basic_auth()
                with pytest.raises(AuthError):
                    am.get_basic_auth()
        finally:
            reset_auth_manager(token)

        # A failed login is not cached as a success; each attempt retries.
        assert calls == ["bob@x.com", "bob@x.com", "bob@x.com", "bob@x.com"]


class TestAtMostOneLoginPerRequest:
    @pytest.mark.asyncio
    async def test_single_login_across_login_tool_kompy_and_rest_calls(self):
        """One credential set ⇒ one login, across all three code paths."""
        auth_tools = _register("auth_tools")
        patcher, calls = _spy()

        am = AuthManager(email="carol@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            with patcher:
                # 1. The login tool.
                await auth_tools["komoot_login"]()
                # 2. A kompy-object call (needs the connector).
                client = KomootClient(am, _NoLimit())
                await client.list_tours()
                # 3. A direct-REST call (needs the Basic-auth pair).
                with patch(
                    "komoot_mcp.client.requests.get",
                    return_value=_resp(json_body={"id": 42}),
                ):
                    await client.get_tour_full(42)
                # 4. The login tool again — must short-circuit.
                again = await auth_tools["komoot_login"]()
                assert again == "Already authenticated as user uid-carol@x.com"
        finally:
            reset_auth_manager(token)

        assert calls == ["carol@x.com"], f"expected 1 login, got {len(calls)}"

    @pytest.mark.asyncio
    async def test_rest_only_flow_logs_in_once(self):
        """Several direct-REST calls with no explicit login ⇒ one login."""
        patcher, calls = _spy()
        am = AuthManager(email="dan@x.com", password="pw")
        client = KomootClient(am, _NoLimit())
        with patcher, patch(
            "komoot_mcp.client.requests.get",
            return_value=_resp(json_body={"id": 1}),
        ):
            await client.get_tour_full(1)
            await client.get_tour_full(2)
            await client.get_tour_full(3)
        assert calls == ["dan@x.com"]

    def test_repeated_login_calls_are_idempotent(self):
        patcher, calls = _spy()
        am = AuthManager(email="erin@x.com", password="pw")
        with patcher:
            am.login()
            am.login()
            am.login()
            connector = am.get_connector()
            assert am.get_connector() is connector
        assert calls == ["erin@x.com"]


class TestBasicAuthUsesTokenNotPassword:
    def test_returns_user_id_and_token(self):
        patcher, _calls = _spy()
        am = AuthManager(email="frank@x.com", password="s3cret-password")
        client = KomootClient(am, _NoLimit())
        with patcher:
            pair = client._basic_auth()
        assert pair == ("uid-frank@x.com", "token-frank@x.com")
        # The account password must never reach the wire as a Basic-auth
        # secret — only the v006 login endpoint accepts it.
        assert "s3cret-password" not in pair
        assert am.email not in pair

    @pytest.mark.asyncio
    async def test_rest_helper_puts_token_pair_on_the_wire(self):
        patcher, _calls = _spy()
        am = AuthManager(email="gina@x.com", password="s3cret-password")
        client = KomootClient(am, _NoLimit())
        captured: dict = {}

        def _fake_get(url, **kwargs):
            captured.update(kwargs)
            return _resp(json_body={"id": 7})

        with patcher, patch("komoot_mcp.client.requests.get", _fake_get):
            await client.get_tour_full(7)

        assert captured["auth"] == ("uid-gina@x.com", "token-gina@x.com")
        assert "s3cret-password" not in captured["auth"]

    def test_auth_headers_and_basic_auth_agree(self):
        """``get_auth_headers`` and ``_basic_auth`` are one identity now."""
        import base64

        patcher, _calls = _spy()
        am = AuthManager(email="hank@x.com", password="pw")
        client = KomootClient(am, _NoLimit())
        with patcher:
            uid, tok = client._basic_auth()
            header = am.get_auth_headers()["Authorization"]
        expected = base64.b64encode(f"{uid}:{tok}".encode()).decode()
        assert header == f"Basic {expected}"


class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_two_context_scopes_never_share_tokens(self):
        """Each ContextVar scope logs in for itself and keeps its own token."""
        patcher, calls = _spy()
        seen: dict[str, tuple] = {}

        async def tenant(email):
            async def inner():
                am = AuthManager(email=email, password="pw")
                set_auth_manager(am)
                client = get_client()
                # Force a login through the direct-REST path.
                with patch(
                    "komoot_mcp.client.requests.get",
                    return_value=_resp(json_body={"id": 1}),
                ):
                    await client.get_tour_full(1)
                seen[email] = (client._basic_auth(), id(am._connector))

            ctx = contextvars.copy_context()
            await asyncio.create_task(inner(), context=ctx)

        with patcher:
            await tenant("ivy@x.com")
            await tenant("jack@x.com")

        assert seen["ivy@x.com"][0] == ("uid-ivy@x.com", "token-ivy@x.com")
        assert seen["jack@x.com"][0] == ("uid-jack@x.com", "token-jack@x.com")
        # Distinct connectors — no shared/global session object.
        assert seen["ivy@x.com"][1] != seen["jack@x.com"][1]
        # One login each: no cross-tenant token cache, no double login.
        assert sorted(calls) == ["ivy@x.com", "jack@x.com"]

    @pytest.mark.asyncio
    async def test_concurrent_tenants_keep_their_own_session(self):
        patcher, calls = _spy()
        results: dict[str, tuple] = {}
        ready = asyncio.Event()
        proceed = asyncio.Event()

        async def tenant(label, email):
            am = AuthManager(email=email, password="pw")
            token = set_auth_manager(am)
            try:
                am.login()
                # Interleave the two tenants mid-flight.
                if label == "kim":
                    ready.set()
                    await proceed.wait()
                else:
                    await ready.wait()
                    proceed.set()
                client = KomootClient(am, _NoLimit())
                results[label] = client._basic_auth()
            finally:
                reset_auth_manager(token)

        with patcher:
            await asyncio.gather(
                tenant("kim", "kim@x.com"),
                tenant("leo", "leo@x.com"),
            )

        assert results["kim"] == ("uid-kim@x.com", "token-kim@x.com")
        assert results["leo"] == ("uid-leo@x.com", "token-leo@x.com")
        assert sorted(calls) == ["kim@x.com", "leo@x.com"]

    def test_clear_request_state_drops_the_session(self):
        """No process-wide credential state survives a request."""
        patcher, _calls = _spy()
        am = AuthManager(email="mia@x.com", password="pw")
        token = set_auth_manager(am)
        try:
            with patcher:
                am.login()
            assert am.is_authenticated()
        finally:
            reset_auth_manager(token)
        clear_request_state()
        # A freshly built manager for the next request starts unauthenticated.
        fresh = AuthManager(email="mia@x.com", password="pw")
        assert not fresh.is_authenticated()
        assert fresh._connector is None
