"""Explicit full-product feedback binding for isolated Outcome conformance tests."""

import pytest


@pytest.fixture(autouse=True)
def configured_outcome_feedback(monkeypatch):
    from aletheia.learning.outcome_feedback import execute_outcome_feedback
    from aletheia.portfolio import outcome_hooks

    monkeypatch.setattr(outcome_hooks, '_feedback_executor', execute_outcome_feedback)
