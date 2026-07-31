"""Counterfactual analysis — decision-alternative ledgers, replays, and experiments.

Concrete implementations of the framework's counterfactual capability
(see docs/design/subsystems/causal_counterfactual_research_guidance.md):

- trades              — L1 fixed-horizon Top-N shadow-trade ledger and its
                        resolvers (which candidate to have picked)
- filter_replay       — L3 bounded read-only replays for monotonic filter
                        thresholds; consumed by filter_experiments and
                        analytics/filter_controller
- filter_experiments  — L4 observe-only prospective threshold experiments
                        (preregistered assignment, evidence gates)
- calibration         — L2 descriptive report: conviction-gate calibration
                        report from resolved shadow rows

Storage-layer helpers for the shadow_trades / filter_shadow_experiments
tables remain in ``aletheia.storage.repositories``; this package is the
application layer.

Realized exit-driver comparison lives in ``aletheia.analytics.exit_driver_comparison``
— it is attribution over actual closes, not a counterfactual, and was moved
out of this package to reflect that distinction.
"""
