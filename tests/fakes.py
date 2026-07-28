"""Explicit test doubles for the two third-party dependencies.

Why this module exists
---------------------
``komoot_mcp.client`` imports ``kompy`` and ``komoot_mcp.routing``
imports ``openrouteservice`` at module scope. Historically the test
suite relied on ``tests/conftest.py`` swapping in *behaving* stub
modules whenever those packages failed to import, which meant a handful
of tests only passed when the real libraries were **absent** — the
suite's result depended on what happened to be installed.

The fakes below are injected explicitly (see the ``fake_kompy_connector``
and ``fake_ors_client`` fixtures in ``conftest.py``) by patching the
attribute on the dependency's module object. Production code resolves
``kompy.KomootConnector`` / ``openrouteservice.Client`` at call time, so
the patch takes effect identically whether the module is the real
package or the import stand-in from :func:`make_kompy_stub_modules` /
:func:`make_openrouteservice_stub_modules`.
"""
from __future__ import annotations

from types import ModuleType
from typing import Any


# --------------------------------------------------------------------------
# kompy doubles
# --------------------------------------------------------------------------


class FakeTour:
    """A plain object carrying the attributes ``_tour_to_dict`` reads."""

    def __init__(self, tour_id=1, name="fake"):
        self.id = tour_id
        self.name = name
        self.sport = "hike"
        self.status = "private"
        self.distance = 5000
        self.elevation_up = 200
        self.elevation_down = 200
        self.duration = 3600


class FakeAuthentication:
    """Mirrors ``kompy.Authentication``'s accessor surface."""

    def __init__(self, email, password):
        self._email = email
        self._password = password
        self._username = None
        self._token = None

    def get_username(self):
        # Mirror real kompy behavior: raise if login hasn't populated
        # the username, so we catch any regression that bypasses login.
        if self._username is None:
            raise ValueError("No username set, please login first.")
        return self._username

    def get_email_address(self):
        return self._email

    def get_password(self):
        return self._password

    def set_username(self, username):
        self._username = username

    def set_token(self, token):
        self._token = token


class FakeKomootConnector:
    """In-memory stand-in for ``kompy.KomootConnector``.

    The real connector performs a login HTTP call in ``__init__`` and
    populates ``self.authentication`` with the username/token. We mirror
    that shape (without any network) so ``get_user_profile`` and
    ``list_tours`` work, and so the per-request credential threading can
    be asserted through the returned tour names.
    """

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.authentication = FakeAuthentication(email, password)
        self.authentication.set_username(f"user-{email}")
        self.authentication.set_token("fake-token")

    def get_tours(self, **kwargs):
        return [FakeTour(1, f"tour-for-{self.email}")]


# --------------------------------------------------------------------------
# openrouteservice doubles
# --------------------------------------------------------------------------


class FakeOrsClient:
    """Recording stand-in for ``openrouteservice.Client``.

    Exposes the key under the public ``key`` attribute (the real client
    keeps it private as ``_key``) so tests can assert that the
    per-request ORS key was threaded into the constructor. Also mirrors
    the private attributes production code reads
    (``_base_url``/``_timeout``, used by ``RoutingManager._fetch_gpx``).
    """

    def __init__(
        self,
        key=None,
        base_url="https://api.openrouteservice.org",
        timeout=60,
        **kwargs,
    ):
        self.key = key
        self._key = key
        self._base_url = base_url
        self._timeout = timeout
        self.calls: list[dict[str, Any]] = []

    def directions(self, **kwargs):
        self.calls.append(kwargs)
        # Minimal GeoJSON shape the production code reads.
        return {
            "features": [
                {
                    "properties": {
                        "summary": {
                            "distance": 1234.5,
                            "ascent": 67.8,
                            "duration": 909,
                        }
                    },
                    "geometry": {
                        "coordinates": [[13.4, 52.5], [13.41, 52.51]],
                    },
                }
            ]
        }


# --------------------------------------------------------------------------
# Import stand-ins (used only when the real package is not installed)
# --------------------------------------------------------------------------
#
# These exist purely so ``import kompy`` / ``import openrouteservice``
# succeed in a bare environment. They deliberately provide NO useful
# behaviour: anything that would touch the network raises. That keeps the
# suite honest — a test can never pass just because a dependency is
# missing. Tests needing behaviour inject the fakes above.


class UnavailableKomootConnector:
    """Refuses construction — building a connector means logging in."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "kompy is not installed, so no Komoot connector can be built. "
            "Tests that need connector behaviour must inject "
            "fakes.FakeKomootConnector (see the 'fake_kompy_connector' "
            "fixture) rather than relying on the import stand-in."
        )


class UnavailableOrsClient:
    """Constructs (the real client does too, offline) but never routes.

    Attribute names match the real ``openrouteservice.Client`` so
    production code behaves the same with or without the dependency.
    """

    def __init__(
        self,
        key=None,
        base_url="https://api.openrouteservice.org",
        timeout=60,
        **kwargs,
    ):
        self._key = key
        self._base_url = base_url
        self._timeout = timeout

    def directions(self, **kwargs):
        raise RuntimeError(
            "openrouteservice is not installed. Tests that need routing "
            "behaviour must inject fakes.FakeOrsClient (see the "
            "'fake_ors_client' fixture)."
        )


class StubApiError(Exception):
    """Mirrors ``openrouteservice.exceptions.ApiError``."""


class StubHTTPError(Exception):
    """Mirrors ``openrouteservice.exceptions.HTTPError``.

    The production class is raised by the ORS client when the response
    body can't be JSON-decoded (which used to happen on every successful
    GPX request — see issue #11).
    """

    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"HTTP Error: {status_code}")


def make_kompy_stub_modules() -> dict[str, ModuleType]:
    """Return ``{module_name: module}`` standing in for ``kompy``."""
    stub = ModuleType("kompy")
    stub.KomootConnector = UnavailableKomootConnector
    stub.Authentication = FakeAuthentication
    # ``Tour`` is used for its class identity only — production code does
    # ``isinstance(tour, kompy.Tour)`` and tests build instances with
    # ``Tour.__new__``, so a plain attribute-settable class suffices.
    stub.Tour = FakeTour
    return {"kompy": stub}


def make_openrouteservice_stub_modules() -> dict[str, ModuleType]:
    """Return ``{module_name: module}`` standing in for ``openrouteservice``."""
    stub = ModuleType("openrouteservice")
    stub.Client = UnavailableOrsClient

    exceptions = ModuleType("openrouteservice.exceptions")
    exceptions.ApiError = StubApiError
    exceptions.HTTPError = StubHTTPError
    stub.exceptions = exceptions

    return {
        "openrouteservice": stub,
        "openrouteservice.exceptions": exceptions,
    }
