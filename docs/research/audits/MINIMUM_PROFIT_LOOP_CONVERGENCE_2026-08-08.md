# Minimum Profit Loop Convergence Evidence — 2026-08-08

> **Role:** dated implementation and runtime evidence. This report records what
> was merged and observed during the 2026-08-08 convergence pass. It is not a
> current Runtime status page, a profitability claim, or an authority grant.
> Re-check GitHub, Runtime provenance, Doctor, logs, and the selected data root
> before relying on any mutable fact below.

## 1. Scope and conclusion

This pass kept the MVP boundary fixed on one minimum economic loop:

```text
market scan / Universe
  -> Opportunity
  -> System Operator decision
  -> Proposal -> RiskDecision -> Execution Gateway
  -> paper_internal Order / Fill / Position
  -> position evaluation and protective exit
  -> cost-complete genuine outcome
  -> review / counterfactual / Learning measurement
  -> owner-gated bounded influence on a later decision
```

The deterministic software path is materially more complete after this pass:
exit evidence is fail-closed, Operator ExitPlan mutations can no longer bypass
entry constraints, Learning separates assignment/permission/actual behavior and
uses one prospective fixed look for Paper promotion, new Paper positions have
durable exact-1m protection, and one prospective exit treatment can collect
paired net-PnL evidence without changing real behavior. Follow-up repairs also
preserve scanner direction lineage, keep unresolved horizon reviews durable,
isolate Learning measurement scheduling, and link exact first-stop evidence to
post-trade diagnosis.

This does **not** prove that the loop is profitable. The qualification cohort and
exit-shadow cohort still need natural, cost-aware observations. Activity, a
healthy Runtime, a merged PR, or aggregate Paper PnL cannot substitute for that
evidence.

## 2. Merged PR ledger

All PRs below were Ready PRs and reached `master` through Squash Merge. Their PR
pages and CI runs remain the canonical review record.

| PR | Merge commit | Contribution to the loop | Explicit boundary |
|---|---|---|---|
| [#757 — reduce the protective blind window](https://github.com/iridite/aletheia/pull/757) | `b8621676` | Moved Position Manager to a dedicated executor, matched it to the 30-second exact-instrument ticker cadence, and timestamped each completed ticker fetch honestly. | Sampled point-mark semantics only; did not yet detect 1m wick crossings. |
| [#758 — fail-closed exit path evidence](https://github.com/iridite/aletheia/pull/758) | `0975053b` | Replaced symbol-only 1h reconstruction with exact venue/market 1m evidence, real post-exit windows, conservative boundary bounds, and non-causal labels. | Read-only diagnostics; ambiguous paths cannot advise parameter changes. |
| [#759 — validate Operator exit adjustments](https://github.com/iridite/aletheia/pull/759) | `090446e4` | Revalidates finite values, current-mark geometry, monotonic protection and the original Proposal/Risk reward-risk envelope before persisting an ExitPlan adjustment. | No Risk/Execution/Live/default change; rejected adjustments perform no write. |
| [#760 — distinguish permission from actual intervention](https://github.com/iridite/aletheia/pull/760) | `9b2af12a` | Preserves intent-to-treat while separating assignment, treatment permission and finite behavior change; no-op/refused rows remain visible but cannot satisfy the actual-intervention gate. | Did not solve repeated-look inference by itself; rotated metric/behavior identity. |
| [#761 — protective barrier storage](https://github.com/iridite/aletheia/pull/761) | `be07a872` | Added immutable ExitPlan versions, exact-instrument protection state/cursors, append-only first-trigger evidence and CAS-only close linkage. | Dormant storage only; no evaluator, activation or close authority. |
| [#763 — fixed-look Paper promotion](https://github.com/iridite/aletheia/pull/763) | `faccbd70` | Added an outcome-blind frozen cutoff, one confirmatory decision at authority alpha `0.025`, one-time consumption and separate anytime safety monitoring. | No Paper treatment was admitted; older verdicts remain monitoring-only history. |
| [#765 — owner-aware scheduler resume](https://github.com/iridite/aletheia/pull/765) | `0707b037` | Centralized resume checks under the pause-transition lock so Settings/direct resume cannot bypass durable pause owners. | Only the resume-authority slice of #391; other #391 acceptance gaps remain tracked there. |
| [#766 — exact 1m protective barriers](https://github.com/iridite/aletheia/pull/766) | `d680c11f` | Activates protection for new native Paper positions, evaluates completed exact-instrument 1m bars, records stop-first collisions and routes triggers through the existing fresh-mark close. | `paper_internal` only; no historical backfill or fabricated barrier-price fill. |
| [#767 — prospective entry-lock exit shadow](https://github.com/iridite/aletheia/pull/767) | `9e94ce68` | Freezes one read-only treatment: after a completed bar reaches `+1.5%` favorable return, an entry-price shadow stop becomes active from the next bar; settlement compares paired cost-aware net PnL. | `behavior_authority=0`; this is entry-lock evidence, not guaranteed economic breakeven. |
| [#768 — refresh exact protective bars](https://github.com/iridite/aletheia/pull/768) | `6b62441e` | Repairs the runtime data-supply gap exposed by #766: missing due bars bypass the general OHLCV TTL cache, stay on the frozen venue, and are consumed chronologically with bounded catch-up. | Missing provider evidence still remains a visible gap; no cross-venue substitution. |
| [#769 — synchronize convergence documentation](https://github.com/iridite/aletheia/pull/769) | `22919107` | Aligned Product, Runbook, contracts and this dated evidence ledger with the narrowed loop. | Documentation only; it granted no behavior authority. |
| [#772 — link first-stop evidence to exit review](https://github.com/iridite/aletheia/pull/772) | `cea6e50b` | Joins immutable exact-1m trigger/close evidence to retrospective trade paths and preserves that exact fact even when another path boundary is incomplete. | Analytics only; legacy, changed-plan and unordered evidence remains fail-closed. |
| [#773 — preserve untargeted horizon reviews](https://github.com/iridite/aletheia/pull/773) | `78dd4b47` | Prevents one targeted close/adjustment from acknowledging sibling positions that are still due for review. | Did not change exit policy or the hard backstop. |
| [#774 — preserve scanner direction lineage](https://github.com/iridite/aletheia/pull/774) | `44373a67` | Keeps deterministic scanner direction/score producer-owned while storing later Observer disagreement separately. | Did not change scanner scoring, thresholds or authority. |
| [#775 — reject direction lineage conflicts](https://github.com/iridite/aletheia/pull/775) | `0df53265` | Rechecks immutable scanner direction before Proposal creation and rejects a reversed final decision before price/Risk/Order side effects. | Defense in depth only; no new scoring or execution path. |
| [#776 — forward-adopt open Paper protection](https://github.com/iridite/aletheia/pull/776) | `234272c8` | Enrolls still-open legacy Paper positions from the adoption instant without replaying older bars or contaminating the prospective exit cohort. | Paper only; no historical trigger fabrication. |
| [#778 — require terminal horizon resolution](https://github.com/iridite/aletheia/pull/778) | `bccb3e89` | Keeps a due horizon wake open until a confirmed close or explicit future age extension. | This is a Paper behavior change; deployment defines a new observation window. |
| [#780 — isolate Learning measurement scheduling](https://github.com/iridite/aletheia/pull/780) | `a63f1564` | Moves recurring and boot-catch-up measurement to one dedicated serialized executor, removing startup queue starvation. | Measurement scheduling only; no experiment gate or behavior authority change. |
| [#783 — trace Operator defer observations](https://github.com/iridite/aletheia/pull/783) | `bdce3e68` | Makes durable defer reason/deadline/policy evidence visible in the Opportunity flow instead of misreporting it as an unexplained stall. | Read-only analytics; it does not itself wake a deferred candidate. |

PR #770 is intentionally absent from this functional ledger: it is CI test
isolation work and is tracked by the separate CI task, not as evidence that the
economic loop changed.

### Open Ready candidates at the follow-up checkpoint

The entries below were Ready, squash-auto-merge candidates at
`2026-08-08T13:03Z`; they were **not** merged facts at that checkpoint.

| PR | Intended repair | Evidence still owed |
|---|---|---|
| [#771 — causal gate for Universe scoring](https://github.com/iridite/aletheia/pull/771) | Documents immutable score provenance and a prospective fixed-look evaluation plan. | Documentation only and still requires owner review before any diagnostic implementation; a second approval is required for behavior. |
| [#781 — re-arm due horizon reviews](https://github.com/iridite/aletheia/pull/781) | Re-arms an acknowledged horizon if the currently authored horizon becomes due again. | Merge and post-deployment natural lifecycle evidence. |
| [#785 — wake deferred candidates at their deadline](https://github.com/iridite/aletheia/pull/785) | Adds a durable deadline-derived PI daemon wake independent of signal traffic and heartbeat. | The first declared runtime acceptance deadline was `2026-08-08T14:00Z`; no early/late/busy-loop conclusion is recorded before it. |
| [#787 — review positions with fresh exact marks](https://github.com/iridite/aletheia/pull/787) | Gives portfolio review exact venue/symbol/market mark time, age and provenance, and blocks ordinary mutation without it. | Merge plus a natural post-deployment portfolio-review decision proving the evidence reaches the authoritative action boundary. |

## 3. Resulting capability state

| Loop stage | Current software result | What remains unproven |
|---|---|---|
| Scan and candidate formation | Dynamic Universe, opportunity lineage, fixed/static authoritative scorer and continuous shadow twin are wired. Scanner direction is producer-owned and rechecked before Proposal. | Positive economic contribution of Universe family calibration requires a qualified natural cohort. |
| Entry and execution | System Operator -> Proposal -> RiskDecision -> Gateway -> local Paper Order/Fill/Position remains the active causal path. | No claim that current entries produce positive net expectancy. |
| Position and exit | New and forward-adopted open Paper positions receive exact-1m durable barrier evaluation. Operator plan changes fail closed; horizon wakes remain pending until an explicit terminal action. | Exchange-native/live protection is outside this proof; #781 and #787 were still candidates at the follow-up checkpoint. |
| Outcome and diagnosis | Local Paper close accounting, eligible outcomes and exact first-stop linkage are separated from incomplete/non-causal path observations. Operator defer facts are visible rather than inferred. | Most historical stops predate exact trigger evidence; coarse paths still cannot prove a different exit was realizable. |
| Learning | `universe_family_calibration` is the sole measurement-supported path. Actual intervention, ITT, harm, continuity and one fixed confirmatory look are explicit; measurement jobs have a dedicated executor. | A qualified natural result, owner-admitted Paper trial and any actual behavior intervention are absent at this checkpoint. |
| Exit adaptation | The entry-lock exit shadow can produce prospective paired incremental net-PnL evidence. | No assignment/outcome existed at the checkpoint; it has no mutation authority. |

## 4. Dated Fedora checkpoint

At `2026-08-08T07:09:42Z`, the owner-selected Fedora Paper Runtime was observed
on commit `6b62441eee1764593d9d3df25dd1902b35a7b22a`:

- Gateway front door was ready under persistent user `systemd` service;
- execution profile was Local Paper and Live was disabled;
- all three positions carrying the new protection contract were `active` after
  #768 caught their cursors up; no cross-venue bar was substituted;
- the first completed Universe scan on the final build passed 232 assets;
- the final-build `universe_family_calibration` window began with 144 exposure
  rows across 9 prospective assignment units under policy epoch `7bd2d4746bf7`;
- the exit-shadow tables had zero assignments and zero outcomes because no new
  eligible protected position had opened after the final deployment and the
  experiment intentionally performs no historical backfill.

These are historical observations, not current guarantees. In particular, nine
assignment units do not satisfy the seven-consecutive-day and 50-per-arm gates.

### Follow-up Fedora checkpoint

At `2026-08-08T13:03:34Z`, the experimental Fedora Runtime was healthy with zero
service restarts on local composite commit `dcffaffd`; that commit included
unmerged #785/#787 candidates and therefore was not a `master` release claim.

- 70 eligible closes summed to `-9.779297 USDT`, with 19 wins, 51 losses and
  profit factor `0.4497`;
- 40 stop-loss exits summed to `-16.435240 USDT`;
- exact first-stop evidence was available for 4/40 stop exits. Two were
  conservatively classified as `profit_giveback_before_stop`, together losing
  `-0.769520 USDT` after an average pre-stop favorable-excursion lower bound of
  `2.04685%`;
- Learning contribution reporting was fresh and healthy: 24,124 UFC exposures,
  12,322 treatment assignments and zero authority-applied interventions. The
  active experiments remained `shadow`, so zero intervention was expected at
  this checkpoint and was not by itself proof of a broken executor;
- the prospective exit shadow had only six settled outcomes, two behavior-changing
  counterfactuals and `+0.185648 USDT` total incremental net PnL. That sample is
  descriptive and far too small to authorize a threshold or exit-policy change.

These figures are one immutable observation of the experimental data root. They
do not establish profitability, causal scanner alpha, or Learning effectiveness.

## 5. Qualification and reset semantics

The final declared qualification window begins at the first eligible exposure
from the final deployed build, not at experiment registration or PR merge time.
Evidence from earlier builds remains immutable history and is excluded from the
declared final-build checkpoint.

The behavior contract, not a convenient narrative, controls statistical cohort
compatibility. Nevertheless, deploying a new build to the qualification Runtime
requires an explicit owner decision and a fresh current-build window under the
current Qualification protocol. No document or manual query may pool pre/post
behavior, bridge a missing UTC day, or convert monitoring-positive evidence into
promotion authority.

## 6. Open evidence questions for the resumed long-term goal

The next iteration should remain a gradient against the largest observed defect,
not a horizontal feature expansion:

1. Does exact-1m protection stay current without persistent gaps, job delay, or
   close/accounting divergence over natural holding periods?
2. Does the prospective entry-lock treatment improve paired incremental net PnL
   after fees, slippage and applicable funding, or does it merely move losses?
3. Can the final UFC window satisfy continuity, 50-per-arm, actual-intervention,
   harm and fixed-look integrity without manual sample selection?
4. If an owner admits `paper_10`, does the consumed confirmatory result support a
   bounded multiplier while the permanent control holdout remains credible?
5. Which single observed stage then contributes the largest attributable loss or
   evidence invalidation? That stage becomes the next repair target.
6. After #785/#787 merge, do deadline wakes and fresh portfolio marks produce
   durable authoritative decisions at the promised boundaries without an early
   evaluation, late heartbeat fallback, stale-mark mutation or busy loop?

Hard Risk, Live, credentials, direct execution authority and unrelated new agents,
markets, pages or infrastructure remain outside this convergence step. Paper
capacity stays deliberately wide unless it breaks protective-exit latency,
accounting consistency, or causal attribution.

## 7. Canonical references

- Product direction: [`DIRECTION.md`](../../product/definition/DIRECTION.md)
- Current scope: [`SCOPE.md`](../../product/SCOPE.md)
- Admission gates: [`PHASE_GATES.md`](../../product/gates/PHASE_GATES.md)
- Metric meaning: [`METRICS.md`](../../product/gates/METRICS.md)
- Experiment rules: [`EXPERIMENT_PROTOCOL.md`](../../product/experiments/EXPERIMENT_PROTOCOL.md)
- Learning behavior: [`learning_contract.md`](../../design/contracts/learning_contract.md)
- Exit behavior: [`execution_contract.md`](../../design/contracts/execution_contract.md)
- Durable protection/exit-shadow state: [`data_model.md`](../../design/data/data_model.md)
- Runtime/deployment isolation: [`QUALIFICATION_DEVELOPMENT_PROTOCOL.md`](../../operations/development/QUALIFICATION_DEVELOPMENT_PROTOCOL.md)

Ownership, priority, closure and remaining defects continue to live in GitHub,
especially issues [#391](https://github.com/iridite/aletheia/issues/391),
[#447](https://github.com/iridite/aletheia/issues/447) and
[#470](https://github.com/iridite/aletheia/issues/470).
