# Universe Baseline Integrity Contract

Parent economic authority: #2556
Baseline branch point: `master@a48c4f42e2d97882278b0e18bdd6ef7e3421bfc4`

## Purpose

Before any Universe family competition or Discovery Lift experiment, freeze a truthful, lossless measurement view of the current `universe-step-v3` authority without silently promoting a preferred challenger.

This contract fixes pre-experiment integrity problems only. It does not claim that the current family taxonomy, absolute step score, Relative Rank challenger, Derivatives feature set, or simple Momentum baseline is economically optimal.

## Required invariants

1. The production `scanner_family`, `scanner_direction`, `opportunity_score`, allocator score, thresholds, quotas, Candidate admission, Risk, Entry Plan and execution behavior remain unchanged by this contract.
2. All registered scanner families remain observable with their own score, direction and reasons even when they do not win the primary-family competition.
3. Family semantic roles are explicit because the current machine IDs mix different conceptual layers:
   - `momentum`: directional return archetype;
   - `mean_reversion`: directional return archetype;
   - `volatility`: market-state-conditioned directional proxy;
   - `derivatives`: positioning/evidence-domain proxy;
   - `event`: market-shock proxy, not a proven external event/news detector;
   - `short_bias`: directional specialization.
4. `event` keeps its historical machine ID for compatibility, but every pre-competition measurement surface must label it `Market Shock / Event Proxy` and record that true event/news/attention evidence is unavailable.
5. Derivatives v1 explicitly declares the missing capabilities `basis`, `liquidation`, and `cross_venue_positioning`; missing capabilities are not fabricated as zeros and are not added before the family competition.
6. The current absolute step scorer remains the frozen control. `relative-rank-shadow-v1` remains a challenger until prospective evidence decides otherwise.
7. Whether the complex Universe beats a simple Momentum baseline remains an experiment question, not a preflight assumption.

## Acceptance

- A pure read-only adapter returns a bounded evidence vector for every family with score, direction, reasons, applicability, semantic role and behavior version.
- The adapter proves its score vector matches the authoritative `all_scores` exactly for the same feature input.
- The adapter preserves production winner-take-all authority while making the information loss explicit and recoverable for measurement.
- Event semantics cannot claim real event/news/attention evidence.
- Derivatives capability gaps are explicit.
- Focused deterministic tests pass and no production behavior path imports the adapter.
