"""UI contract: local paper must not look like a failed exchange connection."""

from pathlib import Path


def test_local_paper_does_not_probe_or_render_an_exchange():
    root = Path(__file__).resolve().parent.parent
    overview = (root / "frontend/src/pages/Overview.tsx").read_text(encoding="utf-8")
    app = (root / "frontend/src/App.tsx").read_text(encoding="utf-8")
    pnl_zone = (root / "frontend/src/components/PnLZone.tsx").read_text(encoding="utf-8")
    account_snapshot = (root / "frontend/src/components/AccountSnapshot.tsx").read_text(encoding="utf-8")

    assert "uses_external_exchange" in overview
    assert "enabled: usesExternalExchange" in overview
    assert "Local paper · no exchange" in overview
    assert "Local paper · no exchange" in app
    assert "execution_target === 'local_paper'" in pnl_zone
    assert "syncStatus === 'not_applicable'" in account_snapshot
