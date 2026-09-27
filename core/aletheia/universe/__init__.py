"""Market evidence, Universe membership and Candidate selection for Entry planning.

service.UniverseService.refresh collects and publishes the existing Universe;
candidate_selection_utility.build_candidate_selection_utility and
select_admitted_candidates evaluate admission from those facts. Storage owns
persisted facts; Entry Planner owns Plans, and Risk/Gateway own execution.
This package does not revive the retired Market Observer or submit orders.

Check: uv run pytest tests/test_candidate_selection_utility.py -q
Contract: docs/design/contracts/candidate_selection_utility_contract.md.
Task routing: docs/design/architecture/SYSTEM_MAP.md.
"""
