from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overview_agent_feed_uses_remaining_viewport_height() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    feed = (ROOT / "frontend/src/components/AgentFeed.tsx").read_text(encoding="utf-8")

    assert "ResizeObserver" not in overview
    assert "SINGLE_COL_CAP" not in overview
    assert "feedCap" not in overview
    assert "<AgentFeed />" in overview
    assert "ref={setRailEl}" not in overview

    assert "getBoundingClientRect().top" in feed
    assert "window.innerHeight" in feed
    assert "Math.floor(viewportHeight - Math.max(0, top) - VIEWPORT_BOTTOM_GAP)" in feed
    assert "MIN_FEED_HEIGHT" in feed
    assert "window.addEventListener('resize', scheduleMeasure)" in feed
    assert "window.visualViewport?.addEventListener('resize', scheduleMeasure)" in feed
    assert "ResizeObserver" in feed
    assert 'className="card agent-feed agent-feed-viewport"' in feed
    assert '<div className="feed-list feed-list-scroll">' in feed
    assert "const visible = items.slice(0, 40)" in feed


def test_overview_rail_uses_canonical_responsive_breakpoints() -> None:
    """The Cockpit main+rail follows the canonical `lg` breakpoint (1024px)
    documented in ui_design_system.md §3.x. Below that the rail stacks as a
    single flex column — NO CSS multicol / masonry (that anti-pattern is
    explicitly banned because collapsibles reshuffle siblings between
    columns and reading order goes Z-shaped)."""
    css = (ROOT / "frontend/src/index.css").read_text(encoding="utf-8")
    cockpit_css = css.split(
        "/* ---------- Cockpit main: feed (main) + right rail ----------", 1
    )[1].split("/* Right-rail cards", 1)[0]

    # Main+rail grid uses the shadcn-style shrink-guard main column + clamped rail.
    assert "grid-template-columns: minmax(0, 1fr) clamp(300px, 26vw, 380px)" in cockpit_css
    # Canonical lg breakpoint (Tailwind alignment: sm 640 / md 768 / lg 1024 / xl 1280).
    assert "@media (max-width: 1023px)" in cockpit_css
    assert ".cockpit-main { grid-template-columns: 1fr; }" in cockpit_css
    # Anti-pattern guards: no multicol masonry anywhere in the block.
    assert "column-count" not in cockpit_css, "column-count masonry is banned for card grids"
    # The AgentFeed viewport-follow behaviour is unchanged.
    assert ".agent-feed-viewport" in css
    assert ".agent-feed-viewport .feed-list-scroll" in css
    assert "overflow-y: auto" in css


# --- Blotter migration (2026-07-18) ---------------------------------------
# The four Overview decision-flow cards (EntryStatusCard / HumanApprovalCard /
# OpportunityFunnelCard / DecisionHistoryCard) plus the standalone
# /proposals Approval Desk have all merged into `pages/Blotter.tsx`. The
# tests below assert the honesty invariants moved with them.
#
# See docs/design/frontend/blotter_design.md for the design rationale and
# docs/design/frontend/webui_page_map.md build step 7 for the migration
# history.

def test_blotter_labels_every_terminal_outcome_without_false_health() -> None:
    blotter = (ROOT / "frontend/src/pages/Blotter.tsx").read_text(encoding="utf-8")

    # Every terminal outcome is a first-class row in the Candidate Ledger —
    # no outcome hidden behind a "successful trades only" default view.
    # (Root cause fix for the "only trades are visible" report — see
    # blotter_design.md #root-cause-only-trades-are-visible.)
    for outcome in ("trade", "operator_skip", "operator_watch",
                    "user_reject", "auto_reject", "reject_unknown"):
        assert outcome in blotter, f"{outcome} missing from Blotter Candidate Ledger"

    # Bloomberg-style short labels, mixed case (not shouty uppercase).
    for label in ("Filled", "Op Skip", "Watch", "User Reject", "Auto Reject", "Reject (?)"):
        assert label in blotter, f"{label} outcome label missing"

    # Honesty tokens the old test guarded still hold: no fake "Healthy" chip
    # and no "Passed gates" label pretending the ranker is a health signal.
    assert '"Healthy"' not in blotter
    assert '"Passed gates"' not in blotter


def test_blotter_replaces_overview_decision_cards() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    blotter = (ROOT / "frontend/src/pages/Blotter.tsx").read_text(encoding="utf-8")
    client = (ROOT / "frontend/src/api/client.ts").read_text(encoding="utf-8")

    # Overview no longer renders or defines any of the four migrated cards —
    # it only keeps a 1-row summary strip pointing at /blotter. We check for
    # actual JSX/function usage (`<Card` or `function Card`) rather than raw
    # substring — the migration comment legitimately mentions them by name.
    for gone in ("EntryStatusCard", "HumanApprovalCard",
                 "OpportunityFunnelCard", "DecisionHistoryCard"):
        assert f"<{gone}" not in overview, f"{gone} JSX still rendered in Overview.tsx"
        assert f"function {gone}" not in overview, f"{gone} still defined in Overview.tsx"
    assert "FUNNEL_WINDOWS" not in overview
    # "Why no position now" was the consumer-fintech phrase we replaced —
    # ensure the raw string isn't rendered anymore (a comment reference is
    # OK if wrapped in a code fence, so we grep only for JSX-level uses).
    assert 'kicker">Why no position now' not in overview
    assert '>Why no position now<' not in overview
    assert "BlotterSummaryStrip" in overview
    assert 'navigate(\'/blotter\')' in overview or '"/blotter"' in overview

    # Blotter now hosts the pipeline diagnosis + human approval queue.
    assert "current.state" in blotter or "current?.state" in blotter
    assert "Awaiting Sign-off" in blotter
    assert "Candidate Ledger" in blotter

    # Client uses the fast /current-snapshot endpoint (perf item 1).
    assert "getOpportunityFunnelCurrentSnapshot" in client
    assert "opportunity-funnel/current-snapshot" in client


def test_blotter_exposes_ranker_filter_stage_and_retry_reason() -> None:
    """Diagnostic honesty: candidate rows still carry the durable blocker,
    passed-filter count, and retry time so the operator sees WHY a candidate
    is stuck, not just THAT it's stuck. Migrated from EntryStatusCard 2026-07-18."""
    blotter = (ROOT / "frontend/src/pages/Blotter.tsx").read_text(encoding="utf-8")

    assert "ranker_cooldown" in blotter
    assert "blocking_filter" in blotter
    assert "passed_filter_count" in blotter
    assert "retry_in_sec" in blotter


def test_blotter_surfaces_unattributed_rejections() -> None:
    blotter = (ROOT / "frontend/src/pages/Blotter.tsx").read_text(encoding="utf-8")
    client = (ROOT / "frontend/src/api/client.ts").read_text(encoding="utf-8")

    # reject_unknown remains an outcome-first-class citizen in both surface
    # and API contract — no silent classification into user/auto rejects.
    assert "reject_unknown" in blotter
    assert "Reject (?)" in blotter  # Blotter's short label for the bucket
    assert "reject_unknown" in client
