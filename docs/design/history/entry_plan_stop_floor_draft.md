# Entry Plan DRAFT: ATR-relative stop-floor policy

> **History (moved 2026-10-10).** Moved verbatim from
> `docs/design/contracts/entry-plan-contract.md` ("Executable static feasibility"). This is a
> default-off draft switch and its rationale, not current approved behavior; the Contract keeps
> only the default (`trading.stop_floor_policy = "vol_target_pct"`).

### DRAFT: ATR-relative stop-floor policy (decision issue #3743, owner-requested, 2026-09-30)

A prior investigation replayed 164 real planning attempts through the compiler's
own arithmetic: the effective floor (`trading.vol_target_pct` = 1.5% of price)
is ~4.1x the median 15m ATR (0.097–1.233% of price, median 0.365%), and every
rejected/accepted attempt separated perfectly at the 1.5% line. Modelled
plan-rate under a `k x ATR` floor: k=0.5 -> 91%, k=1.0 -> 77%, k=1.5 -> 62%,
k=2.0 -> 46%, k=3.0 -> 26%, k=4.0 -> 9%, versus 16% today.

The owner accepted, as a **draft**, a config switch
`trading.stop_floor_policy` (`"vol_target_pct"` default | `"atr_multiple_draft"`)
with `trading.stop_floor_atr_multiple_draft` (default `1.0`) as the `k`
multiplier. The default preserves today's approved semantics exactly — same
floor, same rejection strings, same rechecked-at-execution behavior described
above. Under the draft policy the floor becomes `k * <15m ATR%>`, falling back
to the static `vol_target_pct` floor whenever ATR is unavailable, non-finite,
or non-positive; the floor never loosens without a measurement. The
published-sideways doubling still applies to the resulting draft floor.

Risk rechecks the draft floor at execution against the instrument's *current*
measured 15m ATR% (`aletheia.trading.sizing._lookup_atr_pct`, the same
`universe_feature_snapshots` table the Compiler's `quant_context.atr_pct`
reads, keyed by the Plan's frozen venue/symbol/market_type) — consistent with
the unchanged sentence above that Risk rechecks its *current* floor at
execution, not a value frozen at compile time. An earlier version of this
draft instead froze the evaluated ATR% into a new `EntryPlan.stop_floor_atr_pct`
field; that was rejected because `EntryPlan.model_json_schema()` is part of
the material `entry_contract_fingerprints()` hashes into the entry behavior
and schema fingerprints, so adding any field to `EntryPlan` moves both
fingerprints for every outstanding plan/generation on deploy — an identity
cutover this draft must not cause while its policy defaults off.

**Rollback:** flip `trading.stop_floor_policy` back to
`"vol_target_pct"` (no deploy or code change needed); or `git revert` the DRAFT
commit whose SHA is recorded on decision issue #3743.
