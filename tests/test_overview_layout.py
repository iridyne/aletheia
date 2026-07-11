from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overview_agent_feed_uses_natural_height() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    feed = (ROOT / "frontend/src/components/AgentFeed.tsx").read_text(encoding="utf-8")

    assert "ResizeObserver" not in overview
    assert "SINGLE_COL_CAP" not in overview
    assert "feedCap" not in overview
    assert "<AgentFeed />" in overview
    assert "ref={setRailEl}" not in overview

    assert "maxHeight" not in feed
    assert "feed-list-scroll" not in feed
    assert '<div className="feed-list">' in feed
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
    assert ".feed-list-scroll" not in css
