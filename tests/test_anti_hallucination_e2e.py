"""E2E: anti-hallucination attribution chain (R0) — opportunity → context → decision.

Locks the R0 fix end-to-end (LEARNING_LOOP_AUDIT_2026-06-26 §R0): a seeded open
opportunity must surface into the System Operator's Layer-1 context WITH its id +
scanner_family, and when the PI picks that symbol+direction,
_attach_opportunity_context backfills the real opportunity_id (+ strategy_id +
scanner_family) onto the decision — instead of the old hard-coded None that severed
the whole learning attribution chain (trade_case_study entry-context / guard
predicates / reasoning_feedback scanner all went 'unknown').

Behavioral (DB-backed real functions, no mock-call-verify) — complements the
structural AST guard in spec_learning_pipeline::LP-S05.
"""
import pytest

from tests.test_entry_logic import setup_env
































# test_data_health_signal_win_rate_excludes_unknown_direction was removed in
# #3701 Batch E: FCO enforces direction NOT NULL at the DB level, making the
# NULL-direction scenario impossible. The SQL query also has direction IN
# ('long', 'short'), providing a redundant guard.
