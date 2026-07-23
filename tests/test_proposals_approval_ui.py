from pathlib import Path


def test_proposals_ui_only_offers_paper_approval_for_stored_paper_intent():
    source = Path("frontend/src/pages/Blotter.tsx").read_text(encoding="utf-8")

    assert "function canApprovePaper" in source
    assert "p?.execution_mode === 'paper_internal'" in source
    assert "canApprovePaper(p)" in source
    assert "Approve Local Paper" in source


def test_proposals_ui_does_not_claim_the_backend_overrides_execution_mode():
    source = Path("frontend/src/pages/Blotter.tsx").read_text(encoding="utf-8")

    assert "ALWAYS overrides" not in source
    assert "routes every approval through" not in source
    assert "proposal was tagged" not in source
