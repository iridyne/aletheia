"""Focused regression tests for cross-document Product/Learning truth."""

from scripts.check_docs import semantic_truth_failures


def test_current_document_authorities_match_learning_registry() -> None:
    assert semantic_truth_failures() == []
