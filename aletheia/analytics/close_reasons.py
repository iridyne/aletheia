"""Compatibility facade for the central outcome-integrity close-reason registry.

The single source of truth now lives in ``analytics.outcome_integrity``. Is a
position close a genuine trade outcome, or an administrative/synthetic bookkeeping
close?

Synthetic closes are NOT real strategy exits — they close the local row at a
last-known / administrative price, not a strategy-driven fill:

  - reconciled_gone         : reconciler orphan sweep (mark-to-last-price). Before
                              the 2026-07-01 broker-safety fix these were mostly
                              FALSE orphans from a wrong-broker cache read, and the
                              position was re-adopted next cycle (a thrash loop).
  - demo_validation_cleanup : demo-account validation teardown.
  - units_bug_cleanup       : historical units-conversion bug cleanup.
  - retry_after_fix         : one-off remediation close (2026-07-02) — the OKX
                              clOrdId-hyphen bug (fixed in 5e9ceb5) had made close
                              orders fail, leaving positions stuck open; a retry
                              re-closed them at the post-fix market price. Closed
                              because a BUG blocked the earlier close, NOT a strategy
                              exit — so its PnL is not a trade outcome.

Including them in performance metrics (PF / win-rate / drawdown / trade volume)
pollutes the signal — see docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.
Every consumer that reports trade PERFORMANCE must exclude them; consumers that
report raw account state (equity, open positions) do not use this.
"""
from __future__ import annotations

# Compatibility surface for existing consumers.  The registry and classifier now
# live with the complete closure semantics so new reasons cannot drift across two
# competing sources of truth.
from aletheia.analytics.outcome_integrity import (  # noqa: F401
    ADMINISTRATIVE_CLOSE_REASONS,
    MAINTENANCE_CLOSE_REASONS,
    SYNTHETIC_CLOSE_REASONS,
    is_synthetic_close,
)
