from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overview_agent_feed_uses_remaining_viewport_height() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    feed = (ROOT / "frontend/src/components/AgentFeed.tsx").read_text(encoding="utf-8")
    assert "ResizeObserver" not in overview
    assert "AgentFeed" in overview
    assert "getBoundingClientRect().top" in feed
    assert "window.innerHeight" in feed
    assert "MIN_FEED_HEIGHT" in feed
    assert "ResizeObserver" in feed
    assert "const visible = items.slice(0, 40)" in feed


def test_overview_rail_uses_canonical_responsive_breakpoints() -> None:
    css = (ROOT / "frontend/src/index.css").read_text(encoding="utf-8")
    cockpit_css = css.split(
        "/* ---------- Cockpit main: feed (main) + right rail ----------", 1
    )[1].split("/* Right-rail cards", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr) clamp(300px, 26vw, 380px)" in cockpit_css
    assert "@media (max-width: 1023px)" in cockpit_css
    assert ".cockpit-main { grid-template-columns: 1fr; }" in cockpit_css
    assert "column-count" not in cockpit_css


def test_overview_entry_summary_reads_candidate_attention_and_entry_plan_projection() -> None:
    overview = (ROOT / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    backend = (ROOT / "core/aletheia/server/routes/ui.py").read_text(encoding="utf-8")
    assert "EntrySummaryStrip" in overview
    assert "active_entry_plans" in overview
    assert "needs_planning" in overview
    assert "expiring_soon" in overview
    assert "'/universe'" in overview
    assert "pending_proposals" not in overview
    assert "open_opportunities" not in overview
    assert "repo.get_entry_workbench_summary()" in backend


def test_entry_workbench_replaces_dead_manual_review_surface() -> None:
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    workbench = (ROOT / "frontend/src/pages/Universe.tsx").read_text(encoding="utf-8")
    assert not (ROOT / "frontend/src/pages/Blotter.tsx").exists()
    assert 'title="Entry Workbench"' in workbench
    assert "Entry Plans" in workbench
    assert "Candidates" in workbench
    assert "Planning History" in workbench
    assert "Manual Review" not in workbench
    assert '<LegacyRedirect to="/universe" tab="plans" />' in app


def test_entry_workbench_keeps_unavailable_distinct_from_empty() -> None:
    workbench = (ROOT / "frontend/src/pages/Universe.tsx").read_text(encoding="utf-8")
    assert "is unavailable. Counts and rows are not inferred." in workbench
    assert "`No ${humanize(planStatus).toLowerCase()} Entry Plans.`" in workbench
    assert "No Candidates in" in workbench
    assert "fallback_instruments" in workbench
    assert "They are not durable Candidates" in workbench
