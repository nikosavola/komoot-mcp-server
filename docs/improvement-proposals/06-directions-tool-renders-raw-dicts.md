# [correctness] komoot_get_tour_directions renders raw segment dicts and conflates segments with turn-by-turn directions

**Priority:** Medium &nbsp;·&nbsp; **Labels:** bug, correctness

## Summary

`komoot_get_tour_directions` advertises "turn-by-turn directions" but (a) reads Komoot **segments**, not directions, and (b) renders each item by looking up a `text` key that the serializer never produces — so the output is an ugly Python dict repr, not human-readable directions.

## Evidence

Client serializer (`src/komoot_mcp/client.py`):

```python
@staticmethod
def _segment_to_dict(segment):
    return {
        "type": ...,
        "reference": ...,
        "from": ...,
        "to": ...,
    }   # note: NO "text" key
```

Tool renderer (`src/komoot_mcp/tools/data_tools.py`):

```python
for d in directions[:20]:
    if isinstance(d, dict):
        lines.append(f"  {d.get('text', str(d))}")   # 'text' never exists -> str(dict)
```

So the user sees `{'type': ..., 'reference': ..., 'from': ..., 'to': ...}` lines instead of directions. Separately, `get_tour_directions` reads `tour.segments` — Komoot's real turn-by-turn directions come from the `directions` embed (see `get_tour_full`, which requests `directions=v2`), so the tool is also fetching the wrong thing.

## Impact

- The tool is effectively non-functional for its stated purpose; output is confusing to the model and the end user.

## Proposed fix

1. Fetch actual directions via the `directions` embed (the v007 tour endpoint with `_embedded=directions&directions=v2`, already used by `get_tour_full`), and serialize the real fields (e.g. `type`, `cardinal_direction`, `distance`, `way`/`name`, `index`).
2. Render a readable line per step (e.g. `"<n>. <instruction> (<distance> m)"`).
3. If the intent really is segment boundaries, rename the tool/keys to say so and render the actual `type/from/to` fields rather than a nonexistent `text`.
4. Add a test with a realistic directions payload asserting readable output (no `{'type':` dict-repr leakage).

## Acceptance criteria

- Output is human-readable turn-by-turn text.
- No raw dict reprs appear in the tool result.
