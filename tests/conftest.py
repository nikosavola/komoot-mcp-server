"""Test configuration and fixtures.

Determinism contract
--------------------
``pytest`` must produce the same result whether or not the real ``kompy``
/ ``openrouteservice`` packages are installed. Two rules keep that true:

1. An **import stand-in** is installed for a dependency only when the
   real package cannot be imported — ``komoot_mcp.client`` and
   ``komoot_mcp.routing`` import them at module scope, so something must
   be present. Those stand-ins (``tests/fakes.py``) mirror the real
   public shape and refuse to do anything network-ish. They never
   provide convenient behaviour, so no test can pass merely because a
   dependency is missing.

2. Tests that need working dependency behaviour **inject a fake
   explicitly** via the ``fake_kompy_connector`` / ``fake_ors_client``
   fixtures below. Both patch the attribute on the dependency's module
   object, which production code resolves at call time — so the
   injection behaves identically against the real package and against
   the stand-in.

Previously the stubs were installed "only if the import failed" *and*
carried useful behaviour, so seven tests asserted against stub internals
and flipped to failing as soon as the real libraries were installed.
"""
import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_TESTS_DIR, "..", "src"))
# Make ``fakes`` importable from conftest and from test modules regardless
# of how pytest was invoked.
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from fakes import (  # noqa: E402
    FakeKomootConnector,
    FakeOrsClient,
    make_kompy_stub_modules,
    make_openrouteservice_stub_modules,
)


def _install_stand_in_if_absent(package: str, factory) -> bool:
    """Install stand-in modules for ``package`` if it can't be imported.

    Returns ``True`` when a stand-in was installed (i.e. the real
    dependency is absent). Both code paths leave the same *public shape*
    in ``sys.modules``, so tests must not branch on the result.
    """
    try:
        __import__(package)
        return False
    except ModuleNotFoundError:
        pass

    for name, module in factory().items():
        sys.modules[name] = module
    return True


KOMPY_IS_STAND_IN = _install_stand_in_if_absent("kompy", make_kompy_stub_modules)
ORS_IS_STAND_IN = _install_stand_in_if_absent(
    "openrouteservice", make_openrouteservice_stub_modules
)


@pytest.fixture
def fake_kompy_connector(monkeypatch):
    """Install :class:`fakes.FakeKomootConnector` as ``kompy.KomootConnector``.

    ``komoot_mcp.client`` does ``import kompy`` and looks the class up on
    the module at call time, so patching the module attribute reaches the
    production code path without any import-order tricks — and works the
    same with the real kompy installed.
    """
    import kompy

    monkeypatch.setattr(kompy, "KomootConnector", FakeKomootConnector)
    return FakeKomootConnector


@pytest.fixture
def fake_ors_client(monkeypatch):
    """Install :class:`fakes.FakeOrsClient` as ``openrouteservice.Client``.

    ``RoutingManager.__init__`` calls ``openrouteservice.Client(key=...)``,
    so this lets tests assert which key a per-request manager handed to
    its client without depending on the real client's private internals
    (the real class stores the key as ``_key`` and has no ``key``).
    """
    import openrouteservice

    monkeypatch.setattr(openrouteservice, "Client", FakeOrsClient)
    return FakeOrsClient
