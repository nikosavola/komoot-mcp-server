"""Regression tests for ``komoot_get_tour_directions`` (proposal 06).

Two defects are pinned down here:

* The tool rendered raw Python dict reprs. ``KomootClient._segment_to_dict``
  emitted ``{type, reference, from, to}`` — no ``text`` key — while the
  renderer did ``d.get('text', str(d))``, so the fallback always fired and
  users saw ``{'type': ..., 'from': ...}`` lines.
* The client fetched the wrong data entirely: ``tour.segments`` are route
  *composition* boundaries, not navigation instructions. Real turn-by-turn
  data comes from the v007 ``directions`` embed (``directions=v2``), the
  same one ``get_tour_full`` already requests.

NOTE: Komoot's ``directions=v2`` item field names are inferred, not
live-verified (no credentials, and we never probe the live API). The
payloads below are therefore realistic-but-synthetic, and the parser is
tested for tolerance of missing keys and unexpected envelope shapes.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from komoot_mcp.auth import AuthManager
from komoot_mcp.client import KomootClient
from komoot_mcp.tools import data_tools


class _NoLimit:
    async def acquire(self):
        return None


@pytest.fixture
def client():
    am = AuthManager(email="t@x.com", password="pw")
    c = KomootClient(am, _NoLimit())
    # Pre-seed ``_api`` so ``_basic_auth`` doesn't build a real connector
    # (same pattern as tests/test_phase2_tools.py).
    api = MagicMock()
    auth = MagicMock()
    auth.get_username.return_value = "12345"
    auth.get_password.return_value = "long-lived-token"
    api.authentication = auth
    c._api = api
    return c


def _resp(status=200, json_body=None, text=""):
    r = SimpleNamespace()
    r.status_code = status
    r.text = text
    r.json = lambda: json_body
    return r


def _direction(index, dtype, street, distance, cardinal="NE"):
    """One plausible ``directions=v2`` item."""
    return {
        "index": index,
        "type": dtype,
        "cardinal_direction": cardinal,
        "distance": distance,
        "street": street,
        "last_similar": index,
    }


def _hal_payload(items):
    """The nested HAL shape the v007 tour endpoint returns."""
    return {
        "id": 42,
        "name": "Feldberg loop",
        "_embedded": {"directions": {"_embedded": {"items": items}}},
    }


def _build_tool_registry(module):
    registered: dict[str, callable] = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    module.register(_Mcp())
    return registered


# ------------------------------------------------------------------ client

class TestGetTourDirectionsFetch:
    @pytest.mark.asyncio
    async def test_requests_directions_embed(self, client):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _resp(200, _hal_payload([
                _direction(0, "S", "Wiesenweg", 120),
            ]))

        with patch("komoot_mcp.client.requests.get", side_effect=fake_get):
            out = await client.get_tour_directions(42)

        # The v007 tour endpoint with the directions embed — NOT kompy's
        # ``get_tour_by_id`` / ``tour.segments``.
        assert captured["url"] == "https://api.komoot.de/v007/tours/42"
        params = captured["kwargs"]["params"]
        assert "directions" in params["_embedded"]
        assert params["directions"] == "v2"
        assert captured["kwargs"]["auth"] == ("12345", "long-lived-token")

        assert len(out) == 1
        assert out[0]["type"] == "S"
        assert out[0]["street"] == "Wiesenweg"
        assert out[0]["distance"] == 120
        # No segment-shaped keys leak through any more.
        assert "reference" not in out[0]

    @pytest.mark.asyncio
    async def test_flat_items_shape_is_accepted(self, client):
        """Some Komoot collections flatten ``_embedded.items`` to ``items``."""
        payload = {
            "_embedded": {"directions": {"items": [_direction(3, "TR", "Hauptstraße", 250)]}},
        }
        with patch("komoot_mcp.client.requests.get", return_value=_resp(200, payload)):
            out = await client.get_tour_directions(42)
        assert [d["street"] for d in out] == ["Hauptstraße"]

    @pytest.mark.asyncio
    async def test_bare_list_shape_is_accepted(self, client):
        with patch(
            "komoot_mcp.client.requests.get",
            return_value=_resp(200, [_direction(1, "TL", "Bergweg", 80)]),
        ):
            out = await client.get_tour_directions(42)
        assert out[0]["type"] == "TL"

    @pytest.mark.asyncio
    async def test_unexpected_shape_yields_empty_list(self, client):
        """A surprising payload must degrade to "no directions", not raise."""
        for body in ({}, {"_embedded": {}}, {"_embedded": {"directions": {}}}, None, 7):
            with patch("komoot_mcp.client.requests.get", return_value=_resp(200, body)):
                out = await client.get_tour_directions(42)
            assert out == [], body

    @pytest.mark.asyncio
    async def test_partial_and_nondict_items_do_not_crash(self, client):
        payload = _hal_payload([
            {"type": "TR"},                      # no street, no distance
            {"street": "Nebenweg"},              # no type
            {},                                  # nothing at all
            "Turn left at the church",           # not a dict
            ["unexpected"],                      # not a dict, not a string
        ])
        with patch("komoot_mcp.client.requests.get", return_value=_resp(200, payload)):
            out = await client.get_tour_directions(42)
        assert len(out) == 5
        assert out[0]["street"] is None
        assert out[1]["type"] is None
        assert out[3]["text"] == "Turn left at the church"
        # A container item never gets ``str()``-ed into a repr.
        assert out[4]["text"] is None

    @pytest.mark.asyncio
    async def test_nested_way_object_is_flattened_to_a_name(self, client):
        payload = _hal_payload([{"type": "TR", "way": {"name": "Talstraße"}}])
        with patch("komoot_mcp.client.requests.get", return_value=_resp(200, payload)):
            out = await client.get_tour_directions(42)
        assert out[0]["street"] == "Talstraße"


# -------------------------------------------------------------------- tool

class _FakeClient:
    def __init__(self, directions=None, segments=None):
        self._directions = directions or []
        self._segments = segments or []

    async def get_tour_directions(self, tour_id):
        return self._directions

    async def get_tour_segments(self, tour_id):
        return self._segments


def _render(monkeypatch, directions):
    registered = _build_tool_registry(data_tools)
    monkeypatch.setattr(
        data_tools, "get_client", lambda: _FakeClient(directions=directions),
    )
    return registered["komoot_get_tour_directions"]


class TestDirectionsToolRendering:
    @pytest.mark.asyncio
    async def test_renders_readable_steps(self, monkeypatch):
        tool = _render(monkeypatch, [
            _direction(0, "S", "Wiesenweg", 120),
            _direction(14, "TR", "Hauptstraße", 250),
            _direction(31, "TSL", "Talweg", 1450),
        ])
        out = await tool(tour_id=42)

        assert "1. Start on Wiesenweg (120 m)" in out
        assert "2. Turn right onto Hauptstraße (250 m)" in out
        # Distances >= 1 km read as kilometres.
        assert "3. Turn slightly left onto Talweg (1.4 km)" in out
        # The core acceptance criterion: no Python dict repr leakage.
        assert "{'type'" not in out
        assert "{" not in out

    @pytest.mark.asyncio
    async def test_steps_are_numbered_by_position_not_payload_index(self, monkeypatch):
        """Komoot's ``index`` is a coordinate offset, not a step number."""
        tool = _render(monkeypatch, [_direction(908, "TL", "Bergweg", 60)])
        out = await tool(tour_id=42)
        assert "1. Turn left onto Bergweg" in out
        assert "908." not in out

    @pytest.mark.asyncio
    async def test_missing_fields_degrade_gracefully(self, monkeypatch):
        tool = _render(monkeypatch, [
            {"type": "TR", "street": None, "distance": None,
             "cardinal_direction": None, "text": None},
            {"type": None, "street": "Nebenweg", "distance": 90,
             "cardinal_direction": None, "text": None},
            {"type": "TL", "street": None, "distance": None,
             "cardinal_direction": "SW", "text": None},
            {"type": None, "street": None, "distance": None,
             "cardinal_direction": None, "text": None},
        ])
        out = await tool(tour_id=42)

        assert "1. Turn right" in out
        assert "2. Continue on Nebenweg (90 m)" in out
        assert "3. Turn left heading SW" in out
        assert "4. Continue" in out
        assert "{" not in out
        assert "None" not in out

    @pytest.mark.asyncio
    async def test_unknown_manoeuvre_code_is_surfaced_verbatim(self, monkeypatch):
        """An unmapped code must show up as-is, never as a wrong manoeuvre."""
        tool = _render(monkeypatch, [
            _direction(2, "XYZ", "Feldweg", 40),
            _direction(5, "sharp_left", "Waldweg", 30),
        ])
        out = await tool(tour_id=42)
        assert "XYZ onto Feldweg" in out
        assert "Turn sharply left onto Waldweg" in out

    @pytest.mark.asyncio
    async def test_prerendered_text_is_preferred_when_present(self, monkeypatch):
        tool = _render(monkeypatch, [
            {"type": "TR", "street": "Hauptstraße", "distance": 250,
             "text": "Rechts abbiegen auf die Hauptstraße"},
        ])
        out = await tool(tour_id=42)
        assert "1. Rechts abbiegen auf die Hauptstraße (250 m)" in out

    @pytest.mark.asyncio
    async def test_empty_directions_message(self, monkeypatch):
        tool = _render(monkeypatch, [])
        assert await tool(tour_id=42) == "No directions found."

    @pytest.mark.asyncio
    async def test_truncates_after_twenty_steps(self, monkeypatch):
        steps = [
            _direction(i, "TR", f"Straße {i}", 100 + i) for i in range(25)
        ]
        tool = _render(monkeypatch, steps)
        out = await tool(tour_id=42)

        assert "(25 steps)" in out
        assert "20. Turn right onto Straße 19 (119 m)" in out
        assert "Straße 20" not in out
        assert "... and 5 more steps" in out

    @pytest.mark.asyncio
    async def test_client_error_is_surfaced_as_message(self, monkeypatch):
        registered = _build_tool_registry(data_tools)

        class _Boom:
            async def get_tour_directions(self, tour_id):
                raise RuntimeError("Resource not found. Check the ID.")

        monkeypatch.setattr(data_tools, "get_client", lambda: _Boom())
        out = await registered["komoot_get_tour_directions"](tour_id=42)
        assert out.startswith("Error getting directions:")


class TestSegmentsTool:
    """The segment data kept its home — under an honest name, rendering the
    fields its serializer actually produces (``type``/``from``/``to``)."""

    @pytest.mark.asyncio
    async def test_renders_type_and_index_range(self, monkeypatch):
        registered = _build_tool_registry(data_tools)
        monkeypatch.setattr(
            data_tools,
            "get_client",
            lambda: _FakeClient(segments=[
                {"type": "Routed", "reference": None, "from": 0, "to": 42},
                {"type": "Manual", "reference": "ref-1", "from": 42, "to": 71},
            ]),
        )
        out = await registered["komoot_get_tour_segments"](tour_id=42)

        assert "1. Routed (path points 0-42)" in out
        assert "2. Manual (path points 42-71)" in out
        assert "[ref ref-1]" in out
        assert "{" not in out

    @pytest.mark.asyncio
    async def test_empty_segments_message(self, monkeypatch):
        registered = _build_tool_registry(data_tools)
        monkeypatch.setattr(data_tools, "get_client", lambda: _FakeClient())
        out = await registered["komoot_get_tour_segments"](tour_id=42)
        assert out == "No segments found."
