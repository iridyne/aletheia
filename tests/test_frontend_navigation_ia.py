import re
from pathlib import Path


FRONTEND = Path("frontend/src")


def _read(rel: str) -> str:
    return (FRONTEND / rel).read_text(encoding="utf-8")


def test_sidebar_places_serenity_under_observe_and_keeps_evolve_to_learning():
    app = _read("App.tsx")
    nav = re.search(r"export const NAV: NavGroup\[\] = \[(.*?)\n\]", app, re.S)
    assert nav, "App.tsx must expose the data-driven NAV array"
    nav_text = nav.group(1)

    observe = re.search(r"group: 'Observe'.*?group: 'Decide'", nav_text, re.S)
    evolve = re.search(r"group: 'Evolve'.*?group: 'Operate'", nav_text, re.S)
    assert observe, "Observe nav group is missing"
    assert evolve, "Evolve nav group is missing"

    assert "{ to: '/serenity-lens', label: 'Serenity Lens' }" in observe.group(0)
    assert "{ to: '/learning', label: 'Learning' }" in evolve.group(0)
    assert "Strategy Lifecycle" not in nav_text
    assert "/strategies" not in nav_text


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
