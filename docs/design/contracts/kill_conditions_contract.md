# Kill Conditions Contract

> **Role:** Contract — current approved behavior only. Target designs live in
> [`../specs/`](../specs/README.md); known mismatches live in GitHub Issues.
> **Prefix:** `KC` · **Owner code:** `core/aletheia/analytics/go_live_scorecard.py`
> Retired narrative: [history](../history/risk_kill_live_contract_narrative.md).

## Scope

Three stop rules for the Bitget Demo stage. K1 (profit factor below 0.5 for two
consecutive 14-day windows → end the experiment) is an owner judgment; K2 (three UTC days
without a new position → the trading loop is stalled) has a calculation function but no
runtime consumer; K3 (Live unlock needs a passing go-live scorecard) is enforced in
production and defined in [live_unlock_contract §4](live_unlock_contract.md). No kill
condition pauses entries or stops a window automatically.

## Entry points

| Entry point | Kind | Caller |
|---|---|---|
| `core/aletheia/analytics/go_live_scorecard.py::compute_scorecard` | call | `core/aletheia/server/routes/live.py::live_unlock` (K3) |
| `core/aletheia/analytics/go_live_scorecard.py::check_kill_condition_consecutive_rounds` | call | none in production (K1; tests only) |
| `core/aletheia/analytics/go_live_scorecard.py::count_consecutive_zero_fill_days` | call | none in production (K2; tests only) |

## Durable state

None. All three read existing outcome and position rows and write nothing.

## Requirements

#### KC-C01 K1 kill floor inside the scorecard
`If the eligible cohort's profit factor is below 0.5, then the scorecard's profit_factor criterion shall have status fail and score 0, which makes it a blocker for Live unlock.`
- WHEN PF < 0.5 THEN the criterion detail reads `below kill threshold 0.5` and its weight stays > 0.

#### KC-C02 K1 consecutive-window count is an owner judgment
`The consecutive-rounds function shall count trailing 14-day windows of eligible closed outcomes whose profit factor is below 0.5, stopping at the first window that is empty or at or above 0.5.`
- Nothing calls it at runtime; the owner applies K1 by reading it during an observation window. It sets no per-window minimum sample, so a count is not a sufficiently sampled profitability verdict.

#### KC-C03 K2 zero-open day count
`The zero-open function shall count consecutive UTC days, up to 30, back from today on which the selected execution mode opened no position.`
- Nothing calls it at runtime. The name keeps the historical `zero_fill`; the unit is a `positions.opened_at` date, not a `fills` row.
- WHEN the selected mode cannot be resolved or the query fails THEN it returns `0` (gap G1).

#### KC-C04 K3 scorecard gate on Live unlock
`When Live unlock is requested, the unlock route shall apply the scorecard gate defined in live_unlock_contract LU-C12.`
- This contract does not restate the gate; LU-C12 is its only definition.

#### KC-C05 No personal stop line
`The Risk configuration shall carry no personal or account-level stop-loss line.`
- Adding one requires an approved [spec](../specs/README.md); claim D8-B03 fails if a `personal_stop_loss_usdt` or `personal_stop_line` field appears.

## Failure semantics

| Condition | Response | Visible as | Unavailable vs zero |
|---|---|---|---|
| Scorecard cohort unreadable (K1 inside scorecard, K3) | red light + blocker | scorecard `blockers`, `cohort_state` | unavailable, never a pass |
| PF criterion raises | score 0, detail `unavailable` | criterion `fail` | — |
| Consecutive-rounds read fails (K1) | returns `consecutive_kill_rounds=0`, warning logged | log only | **reads as 0** |
| Zero-open query fails or mode unresolved (K2) | returns `0`, warning logged | log only | **reads as 0** (gap G1) |

## Authority

Kill conditions only measure; they may not pause entries, change Risk limits, stop or
reset observation windows, or unlock Live.

## Verification

Claim ids are `SpecClaim` ids in `tests/specs/` — a separate namespace from `KC-C<NN>`.

| Requirement | Executable claim / focused test |
|---|---|
| KC-C01 | `spec_kill_conditions.py::D8-S01`, `::D8-B01` |
| KC-C02 | `spec_kill_conditions.py::D8-S02` |
| KC-C03 | `spec_kill_conditions.py::D8-S04`; `tests/test_retired_execution_surfaces.py` |
| KC-C04 | `spec_kill_conditions.py::D8-B02`, `::D8-B02A`, `::D8-B02B`, `::D8-C01` |
| KC-C05 | `spec_kill_conditions.py::D8-B03` |

## Known conformance gaps

- **G1 — K2 reports failure as zero.** `count_consecutive_zero_fill_days` returns `0` on an
  unresolved mode or a query error, which reads as "a position opened today". Target:
  [spec 0003](../specs/0003-futures-only-convergence/spec.md) FC-R09 (draft) makes K2 a
  `candidate_entry_stalled` alert from the health job with an `unavailable` state; it does
  not pause entries.

## Decision references

- Product Direction §8 (failure and retreat) is the source of K1–K3; [Product Direction](../../product/definition/DIRECTION.md#enduring-product-boundaries).
- #2945 (2e69baae9): the doctor probe `_probe_profit_loop` (`loop.profit`) and claim D8-S03 that consumed K2 were deleted.
- Spec 0003 (draft): K2 → FC-R09 alert; K1 → owner judgment recorded in [EXPERIMENT_PROTOCOL](../../product/experiments/EXPERIMENT_PROTOCOL.md); K1 stays manual because too few closed positions exist for a 14-day PF window to mean anything.
- D8-C01 (live_unlock_contract): K3 is the code-level form of "demo performance is the only ticket to Live".
