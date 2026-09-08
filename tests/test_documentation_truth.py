"""Focused regression tests for cross-document Product/Learning truth."""

from pathlib import Path

from scripts.check_docs import semantic_truth_failures


def test_current_document_authorities_match_learning_registry() -> None:
    assert semantic_truth_failures() == []


def test_agents_routes_pr_lifecycle_authority() -> None:
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    assert (
        "[@PR_REVIEW_LIFECYCLE.md](docs/operations/development/agent-guides/"
        "PR_REVIEW_LIFECYCLE.md)"
    ) in agents


def test_no_runbook_fabricates_position_outcome_evidence() -> None:
    """No runbook may hand an operator SQL that grants outcome eligibility.

    ``exit_fill_verified`` and ``outcome_eligible`` default to false and
    ``exit_price_source`` to ``unavailable`` precisely because unproven means
    ineligible.  A runbook that UPDATEs them to true manufactures, from the
    operations side, a fact only the fill path may write -- and the fabricated
    outcome then flows into Risk and Learning attribution.  Diagnostic SELECTs
    over the same columns stay allowed.
    """
    granting = ("outcome_eligible", "exit_fill_verified")
    offenders: list[str] = []
    for runbook in Path("docs/operations/runbooks").rglob("*.md"):
        for block in runbook.read_text(encoding="utf-8").split("```")[1::2]:
            lowered = block.lower()
            if "update positions" not in lowered:
                continue
            named = [column for column in granting if column in lowered]
            if named:
                offenders.append(f"{runbook}: UPDATE positions sets {named}")
    assert offenders == [], (
        "runbook grants Position outcome eligibility by raw SQL: "
        + "; ".join(offenders)
    )
