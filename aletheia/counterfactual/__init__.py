"""Counterfactual analysis — decision-alternative ledgers and experiments.

This package holds the concrete implementations of the framework's
counterfactual capability (see docs/design/subsystems/causal_counterfactual_research_guidance.md):

- trades              — Top-N shadow-trade ledger (which candidate to have picked)
- exits               — exit-driver outcome comparison
- filter_experiments  — observe-only filter threshold experiments

Storage-layer helpers for the shadow_trades / filter_shadow_experiments tables
remain in aletheia.storage.repositories; this package is the application layer.
"""
