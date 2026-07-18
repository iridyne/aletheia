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
    css = (ROOT / "frontend/src/index.css").read_text(encoding="utf-8")
    cockpit_css = css.split(
        "/* ---------- Cockpit main: feed (main) + right rail ---------- */", 1
    )[1].split("/* Go-Live Scorecard", 1)[0]

    assert "grid-template-columns: minmax(0, 1fr) clamp(300px, 26vw, 420px)" in cockpit_css
    assert "@media (max-width: 1100px)" in cockpit_css
    assert "column-count: 2" in cockpit_css
    assert "@media (max-width: 700px)" in cockpit_css
    assert ".cockpit-rail { column-count: 1; }" in cockpit_css
    assert ".agent-feed-viewport" in css
    assert ".agent-feed-viewport .feed-list-scroll" in css
    assert "overflow-y: auto" in css


def test_opportunity_funnel_labels_cohort_and_snapshot_without_false_health() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")

    assert "snapshot.freshness" in overview
    assert "Snapshot stale" in overview
    assert 'label="Queued for Operator"' in overview
    assert 'label="Expired / invalidated"' in overview
    assert "proposal conversion" in overview
    assert 'label="Passed gates"' not in overview
    assert "Healthy:" not in overview


def test_overview_leads_with_current_no_position_reason_and_one_hour_funnel() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    client = (ROOT / "frontend/src/api/client.ts").read_text(encoding="utf-8")

    assert "Why no position now" in overview
    assert "current.state" in overview
    assert "Current blocker" in overview
    assert "Human Approval" in overview
    assert "No human approval required" in overview
    assert "No pending actions" not in overview
    assert "const FUNNEL_WINDOWS = [1, 4, 24]" in overview
    assert "getOpportunityFunnel(1)" in overview
    assert "current:" in client


def test_approval_desk_surfaces_unattributed_rejections() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    client = (ROOT / "frontend/src/api/client.ts").read_text(encoding="utf-8")

    assert "reject_unknown" in overview
    assert "Reject attribution unavailable" in overview
    assert "reject_unknown" in client
