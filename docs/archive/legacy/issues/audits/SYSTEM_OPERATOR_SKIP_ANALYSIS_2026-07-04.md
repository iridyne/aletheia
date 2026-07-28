# System Operator Skip Analysis — 2026-07-04

This note analyzes the four next-priority work items after the Thesis Lineage
provenance repair. It is a diagnostic snapshot, not a live status source. Rerun
the commands in the final section before making product or trading changes.

## Human Summary

The current proposal drought is not caused by Thesis Lineage. Thesis Lineage is
now behaving as a read-only audit layer: it reconstructs existing judgment
artifacts, marks missing fields honestly, and does not create new thesis text.

The active bottleneck is earlier in the trading path:

```text
market observations -> ranker candidates -> System Operator decision
```

The System Operator is mostly skipping because the candidate pool is narrow and
weak. Most momentum candidates are blocked by active guards, some candidates are
not supported by the demo venue, and the few remaining eligible setups are being
rated below the trade threshold after debate.

## Evidence Snapshot

Collected from the local runtime database and read-only CLI commands on
2026-07-04. These are evidence for this analysis, not durable project state.

- Last 12h `pi_decisions`: `skip=120`, `close=3`, `reduce=1`, `trade=0`.
- Last 12h non-debate skips: `102`, average conviction `2.5`.
- Last 12h debate skips: `18`, average conviction `44.1`, range `33-50`.
- Last 3h skip reason buckets: `guard_blocked=21`, `other=17`, `sideways_regime=6`.
- Current ranker eligible candidates observed:
  - `TRX/USDT short`, score `48.86`, mean reversion, factor status `watch`.
  - `SOL/USDT long`, score `-16.22`, short-bias family mismatch, weak candidate.
- Active blocking guards observed: `17` total.
  - Momentum long guards: `12`.
  - Momentum short guards: `4`.
  - Mean-reversion long guards: `1`.
- Market regime observed: `SIDEWAYS`, confidence `56%`, long/short weight `50/50`.
- Thesis Lineage 1d audit observed:
  - `missing_signal_source=0`.
  - `link_required_missing_opp=4`.
  - `shadow_trades.missing_opportunity_id=0`.
  - `universe_forward_outcomes.missing_forward_return_pct=0`.

## Priority 1 — Guard Blocking May Be Too Broad

### What We Know

The live candidate list showed repeated hard blocks such as:

```text
guard_blocked:auto_momentum_long_...
guard_blocked:auto_momentum_short_...
```

The active guard set is dominated by auto-generated momentum-loss guards. The
effect is broad: high-confidence momentum candidates like `BTC/USDT`,
`GRAM/USDT`, `PEPE/USDT`, and `XRP/USDT` can be blocked by many historical
symbol-specific momentum guards at the same time.

Relevant code paths:

- `aletheia/agents/paper_trader_ranker.py` applies `guard_blocked:*` as a hard
  candidate skip.
- `aletheia/knowledge/guards.py` maps `block_candidate=True` to a blocking
  guard hit.
- `aletheia/analytics/guard_promotion.py` can promote guard candidates into
  active blocking guards.

### Risk

The intended behavior is good: stop repeating patterns that recently lost
money. The observed risk is overreach: multiple symbol-specific auto guards may
combine into something that behaves like a family-level ban on momentum. That
can shut down proposal generation even when the market produces fresh signals.

### Recommended Next Work

Audit guard scope before relaxing anything:

1. Count active blocking guards by scanner family and direction.
2. For each blocked candidate, record which guard predicates matched and whether
   the match was symbol-specific, family-wide, or regime-specific.
3. Compare blocked candidates against shadow outcomes. If blocked momentum
   candidates have positive shadow outcomes after the guard was activated, the
   guard is probably too broad or too long-lived.
4. Prefer a diagnostic report first. Do not immediately disable guards, because
   they may be correctly protecting the system from repeated losses.

Suggested implementation shape:

- Add a read-only `inspect guard-impact` or extend existing guard diagnostics.
- Output: active guards, matched candidates, counterfactual shadow results, age
  of guard, and whether the guard still has evidence.
- No risk-limit or execution-path changes.

## Priority 2 — Unsupported Demo Symbols Still Enter the Candidate Pool

### What We Know

The candidate list included symbols with:

```text
skip_reason=okx_demo_symbol_not_supported
```

Examples observed: `NES/USDT`, `HMSTR/USDT`.

This means unsupported symbols are not causing live proposals, but they still
consume ranker and System Operator attention. In a small candidate list, even a
few unsupported rows make the operator see a less actionable market.

Relevant code paths:

- `aletheia/agents/paper_trader_ranker.py` sets
  `okx_demo_symbol_not_supported`.
- `aletheia/agent_shell/trading_context.py` passes ranker candidates into the
  System Operator context.
- `aletheia/agent_shell/trading_decision.py` renders ranker candidates into the
  decision prompt.

### Risk

This is not an execution safety issue because unsupported symbols are skipped
before proposal creation. The issue is decision quality: System Operator sees
several attractive-looking but impossible trades, then correctly becomes more
cautious.

### Recommended Next Work

Keep unsupported candidates visible for diagnostics, but avoid letting them
dominate the decision prompt:

1. Measure how often unsupported symbols appear in top-N candidates.
2. Add an explicit summary line in the operator context, for example:

   ```text
   unsupported_candidates: 2 excluded from executable set
   executable_candidates: 2
   ```

3. Consider sorting executable candidates before unsupported candidates in the
   prompt, while keeping unsupported rows in diagnostics.
4. Do not silently drop unsupported rows everywhere; that would hide an
   upstream universe/exchange coverage problem.

## Priority 3 — Debate May Be Compressing Borderline Setups Below Trade Threshold

### What We Know

The trade threshold is:

```text
_MIN_CONVICTION_TO_TRADE = 55
```

The debate range is:

```text
_DEBATE_RANGE = (40, 80)
```

In the last 12h snapshot, debate-ran skips had conviction range `33-50`, average
`44.1`. Recent examples were `TRX/USDT short` and `NEAR/USDT short`: the model
recognized a setup, debated it, and still ended below 55.

Relevant code path:

- `aletheia/agent_shell/trading_decision.py` runs debate for trade-like actions
  in the 40-80 range.
- Debate adjusts conviction by bull/bear advantage.
- If final conviction is below 55, the decision becomes `skip`.

### Risk

This may be correct behavior. The observed environment was sideways and the
open TRX/NEAR short positions were already underwater, so low conviction is not
automatically a bug.

The real risk is not "debate exists"; it is that the system currently stores
only the final conviction and `debate_ran`. Without enough structured
before/after debate evidence, it is hard to tell whether debate is improving
discipline or mechanically suppressing all borderline setups.

### Recommended Next Work

Do not lower the 55 threshold yet. First make debate impact observable:

1. Persist or audit the initial conviction, bull confidence, bear confidence,
   final conviction, and reason for downgrade.
2. Compare debated skip outcomes with shadow outcomes.
3. If debated skips produce positive shadow returns while non-debated skips do
   not, revisit debate weighting.
4. If debated skips mostly avoid bad trades, keep the current threshold.

Suggested implementation shape:

- Add a read-only audit over existing `pi_decisions` and proposal rationale where
  available.
- If new persistence is needed later, keep it in the decision/proposal path, not
  in Thesis Lineage.

## Priority 4 — Keep Thesis Lineage Semantically Small

### What We Know

The latest Track A implementation follows the intended boundary:

- `aletheia/analytics/thesis_lineage.py` is read-only.
- It has no LLM provider imports or HTTP/model calls.
- It reconstructs deterministic links such as
  `proposal_id -> trade_proposals.opportunity_id`.
- It marks inferred links explicitly with
  `decision_opportunity_id_inferred_from`.
- It reports `missing` / `insufficient_data` instead of creating replacement
  thesis text.
- It separates expected blanks from real chain gaps, for example pending forward
  outcomes versus settled outcomes missing `forward_return_pct`.

Tests also enforce this boundary:

- `tests/test_thesis_lineage.py` includes an AST-level test preventing the read
  model from importing agents, LLM providers, or HTTP clients.
- Focused tests cover deterministic reconstruction and legacy metadata parsing.

### Risk

The main risk is future scope creep. It will be tempting to let audit explain
why a losing or skipped trade happened. That would contaminate the learning loop
with after-the-fact reasoning.

### Recommended Next Work

Do not expand Thesis Lineage now. Use it only as the scorekeeper:

1. Continue daily `thesis snapshot --days 7`.
2. Track new-row metrics after fixes, especially:
   - `link_required_missing_opportunity_id`
   - `stale_missing_pnl_pct`
   - `overdue_pending`
   - `missing_forward_return_pct`
3. Keep any future `Thesis Normalizer` or `Thesis Reviewer` as a separate
   explicit PR, marked as after-the-fact, and not used for trading decisions.

## Recommended Sequence

1. Build a guard-impact diagnostic.
   This is the most likely current blocker because many top candidates are hard
   blocked before the System Operator can trade them.

2. Add unsupported-candidate visibility.
   Keep diagnostics honest, but make the executable set clear to the operator.

3. Add debate-impact observability.
   Do not tune thresholds until there is evidence that debate is suppressing
   profitable skipped candidates.

4. Leave Thesis Lineage alone except for observation.
   It is now the audit layer. It should not become the explanation generator.

## Commands To Reproduce

Use these read-only commands before deciding on a fix:

```powershell
uv run aletheia inspect candidates --limit 20
uv run aletheia inspect candidates --eligible-only --limit 20
uv run aletheia inspect guards --scope all
uv run aletheia inspect regime
uv run aletheia thesis audit --days 1
uv run aletheia thesis audit --days 7
```

Decision distribution snapshot:

```powershell
sqlite3 -header -csv data/app.db "SELECT action, COUNT(*) AS n, ROUND(AVG(conviction),1) AS avg_conviction, MIN(conviction) AS min_conviction, MAX(conviction) AS max_conviction FROM pi_decisions WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%f+00:00','now','-12 hours') GROUP BY action ORDER BY n DESC;"
```

Debate impact snapshot:

```powershell
sqlite3 -header -csv data/app.db "SELECT debate_ran, action, COUNT(*) AS n, ROUND(AVG(conviction),1) AS avg_conviction, MIN(conviction), MAX(conviction) FROM pi_decisions WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%f+00:00','now','-12 hours') GROUP BY debate_ran, action ORDER BY debate_ran, n DESC;"
```
