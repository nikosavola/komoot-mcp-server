"""Tour data tools for Komoot MCP server."""

from komoot_mcp.context import get_client


def _format_gpx_response(label: str, gpx: str) -> str:
    """Wrap GPX XML in a fenced code block with a byte-count header.

    Returns a tool-result string of the shape::

        GPX for <label> (<N> bytes):
        ```xml
        <gpx>...</gpx>
        ```

    The full GPX body is always returned verbatim — callers need the
    complete content to upload back to Komoot or save locally. Real-
    world planned routes routinely exceed 300–500 KB; MCP / JSON-RPC
    handles payloads of that size fine, so no size cap is applied.
    Designed for issue #9: callers behind the multi-tenant gateway
    have no access to the server's filesystem.
    """
    size = len(gpx)
    return f"GPX for {label} ({size} bytes):\n```xml\n{gpx}\n```"


# Manoeuvre-code -> English phrase table for turn-by-turn rendering.
#
# NOTE — UNVERIFIED: Komoot's ``directions=v2`` embed is not publicly
# documented and we never live-probe the API (no credentials in CI), so
# these codes are inferred from Komoot's GPX exports and web client. Both
# the terse codes and hypothetical spelled-out forms are listed; anything
# not in the table falls through to ``_humanize_direction_type``, which
# surfaces the raw token instead of guessing a manoeuvre. If a live
# response ever shows other codes, adding rows here is the whole fix.
_DIRECTION_TYPE_TEXT = {
    "S": "Start",
    "TS": "Start",
    "F": "Finish",
    "TF": "Finish",
    "C": "Continue",
    "TC": "Continue straight",
    "CS": "Continue straight",
    "TL": "Turn left",
    "TR": "Turn right",
    "TSL": "Turn slightly left",
    "TSR": "Turn slightly right",
    "THL": "Turn sharply left",
    "THR": "Turn sharply right",
    "TU": "Make a U-turn",
    "RA": "Enter the roundabout",
    "EX": "Exit the roundabout",
    "FERRY": "Take the ferry",
    "START": "Start",
    "FINISH": "Finish",
    "STRAIGHT": "Continue straight",
    "LEFT": "Turn left",
    "RIGHT": "Turn right",
    "SLIGHT_LEFT": "Turn slightly left",
    "SLIGHT_RIGHT": "Turn slightly right",
    "SHARP_LEFT": "Turn sharply left",
    "SHARP_RIGHT": "Turn sharply right",
    "U_TURN": "Make a U-turn",
    "ROUNDABOUT": "Enter the roundabout",
}

# Phrases that read better with "on" than "onto" in front of a way name.
_DIRECTION_ON_PHRASES = {"Start", "Finish", "Continue", "Continue straight"}


def _humanize_direction_type(raw) -> str:
    """Turn a raw manoeuvre code into a readable verb phrase.

    Falls back to "Continue" when the type is missing entirely (reads
    naturally in front of a way name) and to the raw token — spaced out
    if it looks snake_case — when the code is unknown, so an unmapped
    Komoot code shows up verbatim rather than as a wrong instruction.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "Continue"
    key = raw.strip().upper().replace("-", "_")
    phrase = _DIRECTION_TYPE_TEXT.get(key)
    if phrase:
        return phrase
    if "_" in key:
        return key.replace("_", " ").capitalize()
    return raw.strip()


def _format_direction_distance(value):
    """Render a step distance in metres, or ``None`` if unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    if value >= 1000:
        return f"{value / 1000:.1f} km"
    return f"{int(round(value))} m"


def _format_direction_step(step, position: int) -> str:
    """Render one direction step as e.g. ``3. Turn right onto Hauptstraße (250 m)``.

    Steps are numbered by *position* in the list, not by the payload's
    ``index`` field — Komoot's ``index`` is the coordinate offset along
    the route, which would look like arbitrary jumps to a reader.

    Every field is optional (see ``KomootClient._direction_to_dict``), so
    each part is appended only when present. Critically, nothing here ever
    ``str()``s a dict: the previous implementation did
    ``d.get('text', str(d))`` against a serializer that produced no
    ``text`` key at all, so users saw raw ``{'type': ...}`` reprs.
    """
    if not isinstance(step, dict):
        # The client always hands us dicts; this only guards against an
        # exotic payload, and still refuses to repr a container.
        if isinstance(step, str) and step.strip():
            return f"{position}. {step.strip()}"
        return f"{position}. (unrecognized step)"

    text = step.get("text")
    if isinstance(text, str) and text.strip():
        head = text.strip()
    else:
        head = _humanize_direction_type(step.get("type"))
        street = step.get("street")
        if isinstance(street, str) and street.strip():
            joiner = "on" if head in _DIRECTION_ON_PHRASES else "onto"
            head = f"{head} {joiner} {street.strip()}"
        else:
            cardinal = step.get("cardinal_direction")
            if isinstance(cardinal, str) and cardinal.strip():
                head = f"{head} heading {cardinal.strip().upper()}"

    dist = _format_direction_distance(step.get("distance"))
    if dist:
        return f"{position}. {head} ({dist})"
    return f"{position}. {head}"


def register(mcp):
    @mcp.tool()
    async def komoot_get_tour_coordinates(tour_id: int) -> str:
        """Get the coordinate array (lat, lng, altitude) for a tour.

        Args:
            tour_id: The numeric tour ID
        """
        try:
            coords = await get_client().get_tour_coordinates(tour_id)
            if not coords:
                return "No coordinates found."
            lines = [f"Tour {tour_id}: {len(coords)} coordinate points"]
            for i, c in enumerate(coords[:5]):
                if isinstance(c, dict):
                    lines.append(f"  [{i}] lat={c.get('lat')}, lng={c.get('lng')}, alt={c.get('alt', '?')}")
                elif isinstance(c, (list, tuple)) and len(c) >= 2:
                    alt = c[2] if len(c) >= 3 else '?'
                    lines.append(f"  [{i}] lat={c[0]}, lng={c[1]}, alt={alt}")
            if len(coords) > 5:
                lines.append(f"  ... and {len(coords) - 5} more points")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting coordinates: {e}"

    @mcp.tool()
    async def komoot_get_tour_gpx(tour_id: int) -> str:
        """Return a tour's GPX content inline in the response.

        The GPX XML is embedded directly in the tool result (fenced code
        block) so the caller can read, save, or forward it without
        needing access to the server's filesystem. The full body is
        always returned; the byte-size is reported in the header line.

        Args:
            tour_id: The numeric tour ID
        """
        try:
            gpx = await get_client().get_tour_gpx(tour_id)
        except Exception as e:
            return f"Error downloading GPX: {e}"
        return _format_gpx_response(f"tour {tour_id}", gpx)

    @mcp.tool()
    async def komoot_get_tour_directions(tour_id: int) -> str:
        """Get turn-by-turn directions for a tour.

        Each step renders as ``<n>. <manoeuvre> onto <way> (<distance>)``,
        e.g. ``3. Turn right onto Hauptstraße (250 m)``. Only the first 20
        steps are listed; the remainder is summarized in a trailing count.
        For how a route was composed (auto-routed vs. hand-drawn stretches)
        use ``komoot_get_tour_segments`` instead.

        Args:
            tour_id: The numeric tour ID
        """
        try:
            directions = await get_client().get_tour_directions(tour_id)
            if not directions:
                return "No directions found."
            lines = [f"Tour {tour_id} directions ({len(directions)} steps):"]
            for position, d in enumerate(directions[:20], start=1):
                lines.append(f"  {_format_direction_step(d, position)}")
            if len(directions) > 20:
                lines.append(f"  ... and {len(directions) - 20} more steps")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting directions: {e}"

    @mcp.tool()
    async def komoot_get_tour_segments(tour_id: int) -> str:
        """Get a tour's route segments (how each stretch was composed).

        Segments are route-composition boundaries — e.g. a ``Routed``
        stretch spanning path points 0-42 — NOT navigation instructions.
        Use ``komoot_get_tour_directions`` for turn-by-turn directions.

        Args:
            tour_id: The numeric tour ID
        """
        try:
            segments = await get_client().get_tour_segments(tour_id)
            if not segments:
                return "No segments found."
            lines = [f"Tour {tour_id} segments ({len(segments)}):"]
            for i, s in enumerate(segments[:20], start=1):
                if not isinstance(s, dict):
                    continue
                seg_type = s.get("type") or "?"
                start = s.get("from")
                end = s.get("to")
                line = f"  {i}. {seg_type}"
                if start is not None or end is not None:
                    line += (
                        f" (path points {start if start is not None else '?'}"
                        f"-{end if end is not None else '?'})"
                    )
                ref = s.get("reference")
                if ref:
                    line += f" [ref {ref}]"
                lines.append(line)
            if len(segments) > 20:
                lines.append(f"  ... and {len(segments) - 20} more segments")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting segments: {e}"

    @mcp.tool()
    async def komoot_get_tour_way_types(tour_id: int) -> str:
        """Get the way type breakdown for a tour (road, trail, path percentages)."""
        try:
            way_types = await get_client().get_tour_way_types(tour_id)
            if not way_types:
                return "No way type data found."
            if isinstance(way_types, list):
                lines = [f"Way types for tour {tour_id}:"]
                for w in way_types:
                    if isinstance(w, dict):
                        name = w.get("way_type", "?")
                        frac = w.get("fraction")
                        if isinstance(frac, (int, float)):
                            lines.append(f"  {name}: {frac * 100:.1f}%")
                        else:
                            lines.append(f"  {name}: {frac}")
                    else:
                        lines.append(f"  {w}")
                return "\n".join(lines)
            return f"Way types for tour {tour_id}: {way_types}"
        except Exception as e:
            return f"Error getting way types: {e}"

    @mcp.tool()
    async def komoot_get_tour_surfaces(tour_id: int) -> str:
        """Get the surface breakdown for a tour (paved, gravel, trail percentages)."""
        try:
            surfaces = await get_client().get_tour_surfaces(tour_id)
            if not surfaces:
                return "No surface data found."
            if isinstance(surfaces, list):
                return f"Surfaces for tour {tour_id}:\n" + "\n".join(f"  {s}" for s in surfaces)
            return f"Surfaces for tour {tour_id}: {surfaces}"
        except Exception as e:
            return f"Error getting surfaces: {e}"

    @mcp.tool()
    async def komoot_get_tour_timeline(tour_id: int) -> str:
        """Get the event timeline for a tour."""
        try:
            timeline = await get_client().get_tour_timeline(tour_id)
            if not timeline:
                return "No timeline events found."
            lines = [f"Tour {tour_id} timeline:"]
            for event in timeline[:20]:
                if isinstance(event, dict):
                    lines.append(f"  {event.get('type', 'event')}: {event.get('description', str(event))}")
                else:
                    lines.append(f"  {event}")
            if len(timeline) > 20:
                lines.append(f"  ... and {len(timeline) - 20} more events")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting timeline: {e}"
