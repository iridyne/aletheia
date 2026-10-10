> History (moved 2026-10-10): verbatim from `docs/design/frontend/ui_design_system.md` §4. As of the move, `frontend/src/index.css` has no `.bl-*` selector (only an orphan comment block remains); `.ov-blotter-strip` still exists and is used by `frontend/src/pages/Overview.tsx`.

# Retired `.bl-*` Blotter primitives

### Blotter primitives (`.bl-*`) — legacy, no active page owner

The standalone Decision Blotter was removed by issue #2288. The remaining `.bl-*`
CSS in `frontend/src/index.css` is legacy cleanup debt and must not be used by new
or current owner-facing surfaces; the former class table is in
[`history/blotter_design.md`](blotter_design.md). `.ov-blotter-strip`
(the one-line Overview summary) keeps its `.ov-` prefix because it lives outside
the Blotter page.
