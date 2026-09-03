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
