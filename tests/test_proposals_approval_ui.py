from pathlib import Path


def test_dead_manual_review_ui_is_removed_without_fabricating_authority():
    app = Path("frontend/src/App.tsx").read_text(encoding="utf-8")
    workbench = Path("frontend/src/pages/Universe.tsx").read_text(encoding="utf-8")
    model = Path("aletheia/storage/models.py").read_text(encoding="utf-8")

    assert not Path("frontend/src/pages/Blotter.tsx").exists()
    assert "Manual Review" not in workbench
    assert "requires_manual_review" not in model
    assert '<Route path="/blotter" element={<LegacyRedirect to="/universe" tab="plans" />} />' in app
    assert '<Route path="/proposals" element={<LegacyRedirect to="/universe" tab="plans" />} />' in app


def test_entry_plan_proposals_are_not_emitted_as_legacy_activity():
    backend = Path("aletheia/server/routes/ui.py").read_text(encoding="utf-8")
    assert "WHERE entry_plan_id IS NULL" in backend
    assert '"kind": "entry_plan"' in backend
