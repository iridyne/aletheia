"""Optional Learning evidence, lessons and policy-versioned context.

read_models.build_learning_contribution_projection exposes persisted Lab
measurements without granting treatment authority. Observation Lab owns
experiment state; Learning does not own Risk approval or exchange execution.
Evidence, hypotheses/lessons and policy epochs have separate owning modules;
import those modules explicitly rather than adding package-wide side effects.

Check: uv run pytest tests/test_learning_read_models.py -q
Contract: docs/design/contracts/learning_contract.md.
Task routing: docs/design/architecture/SYSTEM_MAP.md.
"""
