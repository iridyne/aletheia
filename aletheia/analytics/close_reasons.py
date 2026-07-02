"""Single source of truth: is a position close a genuine trade outcome, or an
administrative/synthetic bookkeeping close?

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

SYNTHETIC_CLOSE_REASONS = frozenset({
    "reconciled_gone",
    "demo_validation_cleanup",
    "units_bug_cleanup",
    "retry_after_fix",
})


def is_synthetic_close(reason: str | None) -> bool:
    """True if `reason` is an administrative/synthetic close, not a trade outcome."""
    return str(reason or "").strip() in SYNTHETIC_CLOSE_REASONS
