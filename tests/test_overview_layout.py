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
    assert "Math.floor(viewportHeight - top - VIEWPORT_BOTTOM_GAP)" in feed
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

    assert "grid-template-columns: minmax(0, 1fr) clamp(320px, 22vw, 380px)" in cockpit_css
    assert "@media (max-width: 1100px)" in cockpit_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in cockpit_css
    assert "@media (max-width: 700px)" in cockpit_css
    assert ".cockpit-rail { grid-template-columns: 1fr; }" in cockpit_css
    assert ".agent-feed-viewport" in css
    assert ".agent-feed-viewport .feed-list-scroll" in css
    assert "overflow-y: auto" in css
