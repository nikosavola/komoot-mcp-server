"""AuthManager — per-request Komoot credential holder *and* the single
authenticator for that request.

Multi-tenant safe: each request gets its own AuthManager instance via
the ContextVar machinery in ``komoot_mcp.context``. Falls back to env
vars only when constructed with no explicit credentials (local stdio /
dev mode).

NOTE — ONE LOGIN PER REQUEST, ONE SOURCE OF TRUTH (issue 05):
This class used to perform its *own* ``GET /v006/account/email/{email}/``
while ``KomootClient`` separately constructed a ``kompy.KomootConnector``
— whose ``__init__`` logs in again and keeps its own ``Authentication``
object. That gave us two logins per flow, two divergent token copies,
and, worst of all, a ``komoot_login`` tool that reported the state of a
login no data/tour call ever consulted: it could answer "successfully
authenticated" while every subsequent tour call failed to auth (or the
reverse).

kompy's connector now owns the login and this class owns the connector.
``login()`` builds it exactly once per AuthManager and mirrors the
identity it obtained onto ``self.user_id`` / ``self.token``.
``KomootClient`` borrows that very same connector through
``get_connector()`` and derives its direct-REST Basic-auth pair from
``get_basic_auth()``. Net effect: one login per credential set per
request, one token, and ``komoot_login``'s verdict genuinely predicts
whether later tool calls will authenticate.

Why kompy owns the login rather than the other way round:
``KomootConnector.__init__`` performs the login unconditionally and
offers no way to hand it a token we already hold, so having this class
do the HTTP call itself could never remove the second login — it would
only move it.
"""
import base64
import os

import kompy


class AuthError(Exception):
    pass


class AuthManager:
    def __init__(self, email: str | None = None, password: str | None = None):
        # Explicit credentials win; otherwise fall back to env for stdio/dev.
        self.email = email if email is not None else os.environ.get("KOMOOT_EMAIL")
        self.password = password if password is not None else os.environ.get("KOMOOT_PASSWORD")
        self.user_id = None
        self.token = None
        # The one kompy connector for this request. Per-instance on
        # purpose — never a module global, never a shared cache keyed by
        # credentials — so two tenants can never observe each other's
        # session. See ``komoot_mcp.context``.
        self._connector = None

    def login(self):
        """Authenticate this request's credentials — at most once.

        Idempotent: a ``komoot_login`` call followed by any tour tool
        reuses the connector built the first time instead of hitting
        Komoot's login endpoint again.
        """
        if self._connector is not None:
            return
        if not self.email or not self.password:
            raise AuthError("KOMOOT_EMAIL and KOMOOT_PASSWORD environment variables must be set")

        try:
            # Attribute access on the module (not a ``from kompy import``)
            # so tests can monkeypatch ``kompy.KomootConnector`` and have
            # this call site see the replacement.
            connector = kompy.KomootConnector(self.email, self.password)
        except Exception as e:
            # kompy raises ``ConnectionError('Connection to Komoot API
            # failed. Please check your credentials.')`` on a 403 and
            # ``NotEmailError`` for a malformed address. Both are login
            # failures from the caller's point of view. We deliberately
            # re-raise the *bare* message rather than prefixing it: both
            # consumers already add their own context ("Login failed: …"
            # in ``komoot_login``, "Error listing tours: …" in the tool
            # layer via ``KomootAPIError``), so a prefix here would read
            # as "Login failed: Login failed: …".
            raise AuthError(str(e) or f"login failed ({type(e).__name__})")

        self._adopt(connector)

    def _adopt(self, connector):
        """Mirror the connector's post-login identity onto this manager.

        kompy stores the numeric Komoot user id under
        ``Authentication.get_username()`` and the long-lived API token
        (the ``password`` field of the v006 login response) under
        ``get_token()``.

        ``get_password()`` is emphatically *not* the token — it returns
        the account password kompy was constructed with. Reading it as a
        token is the mistake ``KomootClient._basic_auth`` used to make;
        the pair Komoot's REST API expects for already-authenticated
        calls is ``(user_id, token)``.
        """
        authentication = getattr(connector, "authentication", None)
        if authentication is None:
            raise AuthError(
                "Unexpected login response: connector exposes no authentication"
            )
        try:
            user_id = authentication.get_username()
            token = authentication.get_token()
        except Exception as e:
            # kompy's accessors raise ValueError when login didn't
            # populate them. Surface that as a login failure rather than
            # letting a half-authenticated connector escape.
            raise AuthError(f"Unexpected login response: {e}")
        if not user_id or not token:
            raise AuthError("Unexpected login response: missing user_id or token")
        # Publish all three together so ``is_authenticated()`` is never
        # true for a connector we failed to read an identity off.
        self._connector = connector
        self.user_id = user_id
        self.token = token

    def get_connector(self):
        """Return this request's kompy connector, logging in on first use.

        ``KomootClient`` calls this for the operations where kompy's own
        object model is genuinely required (``get_tours``,
        ``get_tour_by_id``, ``Tour.generate_gpx_track``, ``upload_tour``,
        …). Because the connector is cached here, those calls share the
        single login with the direct-REST helpers.
        """
        if self._connector is None:
            self.login()
        return self._connector

    def get_basic_auth(self):
        """Return the ``(user_id, token)`` pair for HTTP Basic auth.

        This is the identity Komoot's REST API accepts for
        already-authenticated calls; the literal email/password pair only
        works against the v006 ``/account/email/`` login endpoint. Logs
        in on demand so the direct-REST helpers keep working without an
        explicit ``komoot_login`` first.
        """
        if not self.is_authenticated():
            self.login()
        return (self.user_id, self.token)

    def get_auth_headers(self):
        if not self.is_authenticated():
            raise AuthError("Not authenticated. Call login() first.")
        auth_str = base64.b64encode(f"{self.user_id}:{self.token}".encode()).decode()
        return {"Authorization": f"Basic {auth_str}"}

    def is_authenticated(self):
        return self.user_id is not None and self.token is not None

    def get_user_id(self):
        if not self.is_authenticated():
            raise AuthError("Not authenticated.")
        return self.user_id
