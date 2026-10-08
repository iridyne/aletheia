"""The one definition of an Order that is not finished.

Four places used to keep their own copy of this list (reconciliation, stale
cleanup, the coordinator's retry fence, and the native-entry ownership SQL), and
the copies had drifted: ``open`` sat in one and was missing from the other three,
so an ``open`` Order held a Risk slot that nothing was assigned to release.

Every state here has the same contract, and the tests hold it:

* it is converged by ``execution.reconciliation`` from private exchange evidence;
* it is bounded by ``execution.stale_order_cleanup`` (entry side, 48h), which
  ends the Order and its Proposal/Entry Plan once the venue's complete exact read
  proves absence;
* until then it blocks a second submission for the same Proposal and keeps its
  Risk slot.

A state added to ``_ORDER_TRANSITIONS`` that is not terminal must be added here;
``tests/test_order_state_liveness.py`` fails otherwise.
"""

# ``partial`` is retained only for rows written by older builds; brokers persist
# the exchange-normalized ``partially_filled``.
NON_TERMINAL_ORDER_STATES: tuple[str, ...] = (
    "pending",
    "open",
    "submitted",
    "partial",
    "partially_filled",
    "submission_unknown",
    "materialization_missing",
)
