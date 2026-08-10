import re
from pathlib import Path


FRONTEND = Path("frontend/src")


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def test_sidebar_uses_trading_desk_ia_with_global_entries():
    app = _read("App.tsx")
    assert "import { NAV } from './routeManifest'" in app
    nav = re.search(r"export const NAV: NavGroup\[\] = \[(.*?)\n\]", _read("routeManifest.ts"), re.S)
    assert nav, "routeManifest.ts must expose the data-driven NAV array"
    nav_text = nav.group(1)

    assert "group: 'Observe'" not in nav_text
    assert "group: 'Evolve'" not in nav_text
    assert "group: 'Operate'" not in nav_text

    # Each item pattern below allows arbitrary extra keys after `label:`
    # (e.g. `icon: LayoutDashboard`) — this test guards IA (which items go
    # where), not the exact literal shape of a NavItem.
    top = re.search(r"items: \[\s*\{ to: '/', label: 'Overview'[^}]*\},\s*\{ to: '/command-center', label: 'Command Center'[^}]*\},\s*\]", nav_text, re.S)
    research = re.search(r"group: 'Research'.*?group: 'Decision'", nav_text, re.S)
    decision = re.search(r"group: 'Decision'.*?group: 'Portfolio'", nav_text, re.S)
    portfolio = re.search(r"group: 'Portfolio'.*?\{ to: '/learning', label: 'Learning'[^}]*\}", nav_text, re.S)
    bottom = re.search(r"items: \[\s*\{ to: '/learning', label: 'Learning'[^}]*\},\s*\{ to: '/usage', label: 'Usage'[^}]*\},\s*\{ to: '/settings', label: 'Settings'[^}]*\},\s*\]", nav_text, re.S)

    assert top, "Overview and Command Center must be top-level global entries"
    assert research, "Research nav group is missing"
    assert decision, "Decision nav group is missing"
    assert portfolio, "Portfolio nav group is missing"
    assert bottom, "Learning and Settings must be top-level global entries"

    def _has_item(scope: str, to: str, label: str) -> bool:
        return bool(re.search(rf"\{{ to: '{re.escape(to)}', label: '{re.escape(label)}'[^}}]*\}}", scope))

    assert _has_item(research.group(0), "/universe", "Universe Board")
    assert _has_item(research.group(0), "/serenity-lens", "Serenity Lens")
    assert _has_item(research.group(0), "/news", "News Intel")
    # 2026-07-18 Blotter migration: the Decide row now points at /blotter.
    # The old '/proposals → Approval Desk' entry has been retired and its
    # route rewired to `<Navigate to="/blotter" replace />` for legacy
    # bookmarks (verified by test_proposals_route_redirects_to_blotter below).
    assert _has_item(decision.group(0), "/blotter", "Blotter")
    assert "Approval Desk" not in decision.group(0)
    assert _has_item(portfolio.group(0), "/trades", "Trades & Positions")
    assert _has_item(portfolio.group(0), "/risk", "Risk & Controls")
    assert "group: 'Learning'" not in nav_text
    assert "group: 'Settings'" not in nav_text
    assert "Strategy Lifecycle" not in nav_text
    assert "/strategies" not in nav_text


def test_proposals_route_redirects_to_blotter():
    """`/proposals` was retired 2026-07-18 in favour of /blotter. Any lingering
    external bookmark or in-app navigate() call to /proposals must still
    resolve; the route stays wired as a Navigate redirect."""
    app = _read("App.tsx")
    # Redirect is present.
    assert re.search(
        r"<Route\s+path=\"/proposals\"\s+element=\{<Navigate\s+to=\"/blotter\"\s+replace\s*/>\}",
        app,
    ), "Expected `<Route path=\"/proposals\" element={<Navigate to=\"/blotter\" replace />}` in App.tsx"
    # `Proposals` component import must be gone (file deleted).
    assert "from './pages/Proposals'" not in app
    # The Blotter route + lazy component import exist.
    assert "const Blotter = lazy(() => import('./pages/Blotter'))" in app
    assert "<Route path=\"/blotter\" element={<Blotter />}" in app


def test_frontend_has_no_links_to_removed_strategy_lifecycle_or_old_serenity_path():
    offenders: dict[str, list[str]] = {}
    for path in FRONTEND.rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        hits = []
        if "/strategies" in text:
            hits.append("/strategies")
        if 'href="/serenity"' in text or "href='/serenity'" in text:
            hits.append("/serenity")
        if "nav-strategies" in text or "Go to Strategies" in text:
            hits.append("stale command palette strategy nav")
        if "Strategy Lifecycle" in text:
            hits.append("stale strategy lifecycle label")
        if hits:
            offenders[str(path)] = hits

    assert offenders == {}
