"""Core Outcome semantics and deterministic evidence/report projections.

outcome_integrity owns raw/eligible/administrative Outcome distinctions;
position_lifecycle_story.build_position_lifecycle_story assembles read-only
Position evidence. Reports consume storage/replay facts, not trading authority.
Optional research belongs in extensions.analytics; final settlement does not
belong here. Import owning modules explicitly, without report re-exports.

Checks: tests/specs/spec_outcome_integrity.py, tests/test_position_lifecycle_story.py.
Contracts: execution_contract.md and profit_loop_observability_contract.md under
docs/design/contracts/. Task routing: docs/design/architecture/SYSTEM_MAP.md.
"""
