# [correctness] komoot_list_tours silently ignores sort_direction

**Priority:** Low &nbsp;·&nbsp; **Labels:** bug, correctness

## Summary

`komoot_list_tours` and `KomootClient.list_tours` both accept a `sort_direction` argument (documented as `'asc'`/`'desc'`), but the value is **never forwarded to kompy**. Only `sort_field` is passed, so `sort_direction` is a silent no-op — a caller asking for oldest-first gets newest-first with no error.

## Evidence

`src/komoot_mcp/client.py`:

```python
async def list_tours(self, ..., sort_field="date", sort_direction="desc"):
    ...
    kwargs = {
        "limit": limit,
        "page": page,
        "sort_field": sort_field,   # sort_direction never added to kwargs
    }
    ...
    tours = await self._call(api.get_tours, **kwargs)
```

`sort_direction` is accepted at the signature (`client.py:117`) and by the tool (`tools/browse_tools.py`), documented in the tool docstring and README, but dropped.

## Impact

- Misleading API surface: a documented parameter does nothing. Callers can't get ascending order and get no feedback that the option was ignored.

## Proposed fix

1. Verify kompy's `get_tours` parameter name for direction (likely `sort_direction` / `order`) and forward it:
   ```python
   if sort_direction:
       kwargs["sort_direction"] = sort_direction
   ```
2. If kompy does not support it, either drop the parameter from the public signature/docs, or sort client-side after fetch and document the limitation.
3. Add a test asserting the direction reaches the underlying call (or that client-side ordering flips).

## Acceptance criteria

- Requesting `sort_direction="asc"` changes result ordering (or the parameter is removed from the surface).
