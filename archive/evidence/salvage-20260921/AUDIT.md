# #3134 Legacy Integration Branch Salvage / Closure Audit

Audit date: 2026-09-21. Read-only repository audit; no implementation, ref update,
merge/rebase/reset/clean, deployment, runtime restart, credential access or orders.
This report is outside the repository. The handoff was read and copied, not edited.

## Binding and recovery

- Branch: `refactor/3134-product-truth`
- HEAD: `ee62856e96af12968915529ed3fac2992c794b14`
- origin/master and live GitHub master: `ab6e1836d6e4ad1c0efeb23b0cacefae52ddf41f`
- Merge-base: `98113da4d6323ba6abc64dda0d5bf17b950d9fe9`
- Ahead 24 / behind 211. All 24 have `+` in git cherry; this is patch identity,
  NOT evidence that their semantics are absent from master.
- Tracked staged/unstaged changes: none. Only untracked: `HANDOFF_3134_CODEX.tmp.md`.
- Both diff checks clean; final main-worktree state unchanged.
- Base-to-branch: 972 files, +19,891/-10,304.
- Master-to-branch final trees: 1,049 files, +22,151/-18,961.
- `legacy-branch.bundle` contains the complete branch history and passed bundle verify.
- Bundle SHA256: `3b78b36975039997bde0f8a77545f66ff154a6de5484e532d370028a51ba33ba`.
- Original and copied handoff SHA256 both:
  `c6de9f52e1aaef6ab4a6d6c0cb42d82b5b5579841f74f1659c1a91cd78072dc5`.
- `staged.patch` and `unstaged.patch` are empty. Full original patches are in
  `all-commit-diffs.txt`; divergence, cherry, path lists and stats are adjacent.
- Current master was read in the pre-existing clean worktree
  `/Users/yixian/Projects/aletheia-3134-pr66`; no new worktree/branch was created.

## Authority and purpose

Current #3134 body, DoD and Batch60–65 checkpoints were reread. Current source and
current Contracts supersede old branch designs. Owner's explicit audit-only
instruction supersedes the Issue's ongoing implementation mandate.

The old line tried to combine Slim Product truth, logical authority isolation,
large Scheme A source relocation, adapter separation, optional module binding,
Legacy retirement and test/doc/tooling path updates in one integration line.
`880415e` alone changes 635 files (+8,605/-7,504). Much of the later history repairs
the consequences of that relocation. It is not a coherent next bounded batch.

## Commit ledger

A = ALREADY_LANDED_EQUIVALENT; B = SUPERSEDED_BY_CURRENT_ARCHITECTURE;
C = STILL_VALID_AND_MISSING; D = HISTORICAL_OR_NON_BLOCKING; E = UNCERTAIN.

One primary bucket per commit; mixed contents are explicitly qualified. C does
not authorize cherry-picking an entire commit. E is not a discard judgment.
The four large mixed commits lack a complete current-owner mapping of every
semantic hunk; the complete diffs are preserved and examined material hunks are
identified below. This is not an exhaustive proof that every remaining hunk is stale.

| Commit | Bucket | Actual changes and disposition |
| --- | --- | --- |
| 454849e | A | Protective-order/trailing dispatch uses registered account environment rather than venue-name sets. Current `execution/stops.py` has the same demo/testnet/live bounds; Batch8 #3226. Do not transplant. |
| b1cfd7a | A | Adoption config and rejected Proposal fixtures explicitly seed Bitget Demo identity. Current corresponding fixtures carry that identity; Batch9 #3227 normalized the fixture authority. |
| c3dc36e | E | 685 added architecture-check lines: broad Core import bans, package absence matrix, collector binding, writer scanners. Current narrow writer guards and optional seams supersede many checks, but no complete equivalence proof for all startup/package combinations. Paths/assertions must be regenerated from current owners, never copied wholesale. |
| 880415e | E | Mega relocation plus substantive coordinator, materialization ancestry, reconciliation, config-alias, optional scheduler, public acquisition and credential changes. Physical move is stale; provider design has been superseded by Batches48/51/54–65. Proven surviving slices are close capability/lifecycle and canonical settings retirement. Position ancestry/duplicate-owner behavior and optional startup matrix still need separate current-contract/reachability proof. |
| 3c46fa2 | C | Mixed UTA tests: explicit `match_cap_close_fallback` profile support/default-unknown regression remains useful for the surviving close slice. Exact-order duplicate/conflict tests overlap later current adapter repairs; old Extension imports are stale. Retain only the capability test intent. |
| 83cd673 | E | 68-file spec migration plus exact Risk identity, duplicate Position ownership, account collision, same-Plan/Proposal averaging and UTA import tests. Risk semantics already landed. Current materializer still differs in ancestry/cardinality, but a whole-flow proof of accepted per-leg behavior is required before moving those assertions. |
| 5c01393 | E | 195-file regression rewrite: old-package paths, provider/collector fakes, optional scheduler absence, settings alias retirement, exact observations. Canonical settings tests support the proven surviving slice. Other optional absence and feature tests require remapping to current provider contracts; not all constitute missing functionality. |
| e2e4c9c | B | Coverage/planner/watch/dev paths changed from `aletheia` to `core/aletheia`; purely dependent on old Scheme A. Regenerate only after stable current ownership. |
| f7665bb | D | Active Bitget versus frozen venues and three HTTP protocol product prose, plus relocated architecture tree. Current product authority is #3134 and later #3280 docs. No independent code delta; do not restore old physical tree. |
| 0fa688b | D | Contract text documents old layout, Learning/Serenity owners, Risk approvals and explicit close capability. Risk approvals are already current; capability intent is covered by current #3134 name-driven prohibition. Old Contract prose is not authority or an implementation prerequisite. Re-document only a future bounded implementation. |
| 1d08b8d | D | Trust-policy/coverage links follow the relocated packages. No independently missing authority rule demonstrated; current policy must not be rewritten to justify old code. |
| 53b1ff1 | D | Ten retirement history notes, including intermediate and final alias-retirement narratives. Useful archaeology; some describe code absent from master, so cannot be published as current truth. |
| 3f8994b | D | Runbook/data/capability prose and old layout references, including frozen Binance and retired CLI descriptions. Historical/doc synchronization, not evidence to resurrect package moves. |
| 29c332a | D | +2,298 audit-document lines. Explicitly contains old dirty-tree/failed-connectivity/intermediate test facts and incomplete DoD. Archive, not current evidence. |
| fdcb315 | C | Removes duplicate legacy stock settings inputs/request types and tests canonical empty-list rendering; displays missing model protocol honestly. Only the canonical settings slice is proved as a current runtime/control gap; implementation needs its matching backend decoder/writer changes from the mixed mega commit. |
| f06254f | B | Frontend static output moves to `core/aletheia`; API generator module invocation workaround. Current source-root/export handling is different; no old relocation workaround should be imported. |
| 2125cd9 | B | Moves the exact Fill fee-provenance UPDATE behind a new repository helper and broadens a lexical guard. Current Batch50 #3268 explicitly reserves the exact UPDATE to `_verify_durable_fills` with stronger owner checks. The authority is already unique; moving the statement is not a missing boundary. |
| 69e39dc | B | Packaging/deployment/runtime source paths follow old `core/aletheia` tree. No independent wheel/pkg work is required or authorized. |
| 6286e30 | D | Checkbox source-test regex plus generated route descriptions. Useful test cleanup at most; no #3134 closure blocker demonstrated. |
| 3db575d | A | Six Risk/execution fixtures use exact Bitget/OKX snapshot venues. Current Risk uses Proposal venue/market and PL-MD-07 rejects cross-market/venue fallback (Batch1 #3219). Preserve current fixture graph, not old imports. |
| 928a6eb | B | uv cache/release roots and correcting accidental test executable paths introduced by old relocation. Do not recreate the problem to retain its repair. |
| dbb7685 | B | Test references follow old optional/ops package split and current exit-plan mutation name. No independent production delta. |
| aa9cd3b | D | Spec ID collision/registration and observation-only mandate dependency assertion. Useful coverage housekeeping, not a demonstrated production DoD gap. |
| ee62856 | B | Wrapper fixture arity/parameterization follows old fast-stream optional binding changes. Recreate coverage against current helper signature only if that bounded seam is selected. |

Counts: **A 3 / B 7 / C 2 / D 8 / E 4 = 24**.
These count commits, not independent surviving capabilities. The two C commits
support two surviving slices whose backend hunks are inside E commits.

## Semantic dispositions and current gaps

### Proven surviving slice S1: close fallback capability and quote lifecycle

- Current `execution/gateway.py:680` calls `coordinator.close_position`.
- `coordinator.py:483` still selects the fallback with
  `row['execution_mode'] in {'bitget_demo', 'bitget_live_limited'}`.
- Its quote path constructs a concrete provider, invokes `_run(get_ticker(...))`,
  and does not close that provider. Old code used a same-loop coroutine with
  `finally: provider.close()` and explicit UTA capability support.
- #3134 invariants explicitly prohibit venue/product-name selection when typed
  capabilities describe it. The current execution contract preserves the existing
  cancellation-evidence fallback, so no order policy/tolerance change is required.
- `probe.py` executes the current predicate with fake equal cancellation evidence;
  the result is selected by mode name. No submission occurs.
- Future extraction: one small capability/lifecycle batch using today's
  `data.providers` port, not the old `ccxt_provider` binding facade. Preserve
  existing supported profiles, limits, claims and unknown-outcome rules.

### Proven surviving slice S2: canonical observation settings

- `server/routes/settings.py:2180` writes `observed_equities=[]` but does not remove
  `observed_stocks`; `scheduler.py:2777` chooses canonical OR legacy.
- `settings.py:2354–2358,2422` accepts legacy field mutations; SignalsTab has the
  duplicate stock control. `trading/instruments.py:33–35` merges the legacy token
  list back into the canonical token workset.
- Clearing canonical equities therefore still schedules legacy-only TSLA.
  `probe.py` evaluates the actual scheduler expression to prove that counterexample.
- #3134 forbids current Legacy controls/fallbacks. Retain historical decode/read,
  do not rewrite historical facts, and preserve explicit empty saves. This is
  observable behavior, not naming purity.
- Future extraction: a bounded canonical config decode/write/consumer/UI batch;
  take current locked mutation ownership, not a whole old settings file.

### Already covered or superseded

- Risk exact venue/market and explicit self-contained account policy are current.
- Protective-order environment identity and normalized adoption fixtures are current.
- Candidate/Planning/Order/Risk/Fill/settlement writer guards have later bounded
  owners; old generic lexical guards are not automatically stronger.
- Features funding/OI/orderbook/ticker/OHLCV/bootstrap now use provider facts and
  current Core persistence/freshness owners (Batches60–65). Old separate factories,
  package relocations and callback bindings must not replace those owners.
- Coinalyze/FRED code has scoped adapter credential handling under the owner's
  exception; absent API keys leave online verification unproven, not permission
  to read credentials or revive general Secret Store authority.

### E evidence still required

- Position materialization: current `_linked_open_position` uses first matching
  position under symbol/market/direction/mode/strategy/school; the old branch also
  checks primary-order authority, account generation, same Proposal or exact
  same-Plan legs and duplicate-owner cardinality. Need an isolated full
  materialization/replay counterexample and explicit current Contract mapping,
  including accepted multi-leg aggregation, before deciding C versus superseded.
- Optional startup: verify Web/CLI/collector and each affected current optional
  package with import-denial/no-side-effect fakes. Old package-name denial tests
  do not prove current runtime absence safety.
- The large test/spec commits mix those unproven changes with mechanical moves
  and landed work. Neither wholesale salvage nor wholesale deletion is justified.

## Current-master closure audit

There is **no global PASS** claim. Current master has genuine completed sub-seams,
but the complete 15-item DoD is not proven by those sub-seams.

| DoD | Status | Current evidence / remaining gate |
| --- | --- | --- |
| 1 Product Truth | PARTIAL | Current Slim Product declaration exists; full exposed surface/Legacy settings audit not complete. |
| 2 Complete owner/writer/side-effect inventory | PARTIAL | Current inventory and narrow guards exist; their own scope excludes dynamic and some optional ownership proofs. |
| 3 Agnostic Core | PARTIAL | Current Data/Model ports cover many paths; reachable close path still selects concrete quote construction. |
| 4 No name-driven Core decisions | OPEN | S1 directly violates the accepted target. |
| 5 UTA + generic CCXT + thin profiles | PARTIAL | Normalized commands/reports and adapter boundary exist; fallback capability and full native-exception proof remain. |
| 6 HTTP endpoint model surface | PARTIAL | Registry exposes Chat Completions/Responses/Anthropic; current endpoint composition exists. Full exact-head discovery/manual-ID/tier/probe matrix not rerun. |
| 7 Bitget Demo through final Outcome | UNVERIFIED | No exact-head Fedora/runtime/economic qualification in this audit; Batch65 proof regression also exists. |
| 8 #3135 settlement convergence | PARTIAL | Shared close/late-cost owner and strict economics present; full acceptance/runtime evidence absent. |
| 9 #1884 reliable control plane | PARTIAL | Independent readiness, PoolTimeout taxonomy and capacity snapshot exist; real holder attribution/fault recovery qualification not established. |
| 10 Legacy inertness | OPEN | S2 leaves active alias writes and fallback consumers. |
| 11 Frozen/optional absence safety | PARTIAL | Bounded brokerless/optional seams exist; complete startup matrix unproven. |
| 12 Mechanical safety protections | PARTIAL | Current writer/credential/idempotency guards exist; not a universal proof. |
| 13 Scheme A | OPEN | Logical ownership is not stable/proven; old physical move is stale. |
| 14 Exact final-head verification | OPEN | Five scoped checks pass, but two actual Batch65 counterexamples remain; no final regression/architecture signoff. |
| 15 Final independently recoverable closure evidence | UNVERIFIED | This audit is recoverable; a final end-to-end closure checkpoint is not yet possible. |

Scheme A is **NOT READY**. Remaining work is not purely cosmetic.
#1884, #3135 and #2899 remain OPEN and remain respective control-plane,
settlement acceptance and runtime/economic evidence blockers. Open issue state
alone is not the proof: each has the missing evidence described above. This
does not imply none of their code repairs have landed, nor that every independent
logical repair must wait for them. No current Fedora health claim is made.

## Batch65 counterexamples and verification limits

PR #3296 has inline P1/P2 review findings despite its checkpoint saying review
completed clean. That clean-review claim is contradicted by the live review
comments and current source; this audit did not edit GitHub history.

1. Actual `CCXTProvider.get_market_instruments('perp')` loads metadata but does not
   set `_candidate_markets_acquired_at`. Bitget `get_funding_observation` reads that
   absent property into `interval_acquired_at`; `proved_funding_interval` rejects
   a missing timestamp. With no other valid proof, Candidate economics is incomplete.
2. The provider represents swap and dated future as the same canonical symbol and
   market type. `_read_market_metadata` ignores `is_test_or_dated`, overwriting the
   perpetual key. Fake swap multiplier 1 followed by dated multiplier 10 produces
   perpetual multiplier 10. Exact physical identity is lost.

Both were reproduced with actual source function bodies and fake dependencies,
without importing application startup, accessing DB/config secrets or network.
This proves deterministic code counterexamples, not live loss or incident frequency.

Existing current-master focused checks: **5 passed in 14.38s** (see
`focused-tests.txt`): feature metadata conversion, Fill owner, exact fee-source
exception, RiskDecision owner, Candidate owner. The first test covers only the
happy path, so its green result does not refute the counterexamples.

## Disposition and unique next step

**SALVAGE_SMALL_DELTA**, restricted to proved slices S1/S2. Keep the archive and
freeze the old branch; **do not delete it while the four E commits remain**.
No rebase/cherry-pick/merge of the integration line. No evidence supports
BRANCH_STILL_STRUCTURALLY_REQUIRED or the claim that its large move must be finished.

The unique recommended next step, only after the owner elects to resume changes:
start a fresh branch from latest master for the **Batch65 funding acquisition-time
regression** as one bounded repair with one failing-then-passing proof. Do not add
a new architecture seam or do Scheme A. Its separate dated-contract counterexample
remains explicitly open, not hidden inside that batch. S1/S2 are a salvage queue,
not authorization to combine them. This audit itself changes no production code.
