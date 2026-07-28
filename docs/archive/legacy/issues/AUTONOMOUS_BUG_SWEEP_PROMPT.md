# Autonomous Bug Discovery & Fix Session — Aletheia Trading System

## MISSION

You are running a **long, uninterrupted bug discovery and fix session** on the Aletheia
autonomous paper-trading system. Your job is to find and fix real bugs — confirmed by
concrete failure scenarios — across the high-ROI domains listed below.

**Do NOT stop after fixing one bug.** After every fix, continue to the next question in
the same domain, then move to the next domain. Only stop when you have exhausted all
domains in this prompt OR hit an unrecoverable blocker you cannot route around.

---

## BEFORE ANYTHING ELSE

1. Read `AGENTS.md` at the repo root — it contains project-specific coding rules.
2. Run `cat /tmp/aletheia_sweep_progress.txt 2>/dev/null` — if this file exists, you have
   already made progress; read it and **continue from where it left off**, not from the top.
3. Run `git log --oneline -10` to see recent work on the current branch.

---

## CONTINUITY RULES (read carefully — violation kills the session)

- **Never commit to `master` directly.** Branch → commit → PR for every fix.
  ```bash
  git checkout -b fix/<short-slug>
  # make changes
  git add <specific files>
  git commit -m "fix(<domain>): <description>"
  gh pr create --title "fix(<domain>): <description>" \
    --body "$(cat <<'EOF'
  Fixes #<issue>

  ## Root cause
  <one paragraph>

  ## Fix
  <one paragraph>
  EOF
  )"
  ```
- After every confirmed + fixed bug, append one line to the progress file:
  ```bash
  echo "FIXED: <issue#> <domain> — <one-line description>" >> /tmp/aletheia_sweep_progress.txt
  ```
- If a question yields **no bug** (the invariant holds), append:
  ```bash
  echo "CLEAN: <domain>/<question-id> — invariant holds" >> /tmp/aletheia_sweep_progress.txt
  ```
- Before moving to a new domain, run `cat /tmp/aletheia_sweep_progress.txt` to orient yourself.
- If you find something broken but can't fix it in one session, open a GitHub issue:
  ```bash
  gh issue create --title "[bug] <description>" \
    --body "## Symptom\n...\n\n## Root cause hypothesis\n...\n\n## Affected file(s)\n..."
  ```
  Then append `ISSUE_OPENED: #<n> <description>` to the progress file and move on.

---

## WHY THESE DOMAINS (first-principles + survivorship bias)

The bugs we have already fixed were **visible**: failing tests, obvious log errors,
immediate symptom → cause chains. The bugs that have **survived** are in areas that
"look working from the outside":

- The risk engine has never obviously crashed → so nobody audited it.
- The market observer emits signals → so nobody checked whether they're complete.
- Verdicts get written to the learning DB → so nobody checked whether attribution is correct.
- The guard system blocks bad trades → so nobody asked if it's too broad and blocking good ones.

These are the highest-ROI targets: untouched code that is safety-critical or
learning-critical, where a single silent bug has compounding effects over time.

---

## DOMAIN 1 — Risk Engine Integrity [START HERE — highest priority]

**Files:** `aletheia/execution/risk_engine.py`, `aletheia/execution/paper_tools.py`,
`aletheia/execution/gateway.py`

**Why highest priority:** A single bypass path here causes real financial loss. No prior
audit. No dedicated spec tests.

### First-principles invariants that MUST hold

1. Risk checks must be **atomic with order submission** — no window where check passes
   but state changes before order is written.
2. Every path that writes a `trade_proposals` row must have gone through risk gate.
3. Leverage caps must apply **identically** for `paper_internal` and `paper_exchange` modes.
4. A rejected risk check must leave **zero side effects** (no partial proposal row, no
   position row, no fill row).
5. Concurrent calls to `evaluate_trading_opportunity()` must not both pass the same
   per-symbol or per-direction limit simultaneously.

### Questions (answer each; if confirmed bug → fix cycle)

**R1.** Read `check_risk()` (or equivalent). Is the check and the subsequent write wrapped
in a single `transaction(immediate=True)`? If not, is there a TOCTOU window where two
concurrent callers both pass the same open-position limit?

**R2.** Search for every call site that writes a `trade_proposals` row
(`grep -rn "insert_trade_proposal\|trade_proposals" aletheia/`). Does each one call
through the risk gate, or is there any path that bypasses it (e.g. `adopt_position`,
`reconcile_positions`, direct DB insert in tests that leak to prod code)?

**R3.** For futures orders: find where `leverage` is applied. Is there a code path where
`leverage=None` is silently treated as `leverage=1` (or worse, passed to the exchange as
null)? Check `execute_live_order` and `prepare_decision_as_proposal`.

**R4.** What happens on risk rejection? Trace the rejection path: does it cleanly return
without writing anything, or does it write a `filter_decisions` row but leave
`trade_proposals` in a partial state?

**R5.** Is there a maximum daily-loss or max-drawdown check? If yes: is it computed from
`positions + open_proposals` (correct) or just `positions` (misses pending proposals)?

---

## DOMAIN 2 — Market Observer Signal Completeness [second priority]

**Files:** `aletheia/agents/market_observer.py`, `aletheia/agents/stock_observer.py`,
`aletheia/agents/equity_observer.py`, `aletheia/storage/repositories.py` (opportunity writes)

**Why high priority:** Garbage signals → all downstream decisions are noise. The observer
"works" (it writes rows) but nobody has checked whether those rows are complete and correct.

### First-principles invariants

1. Every written `opportunities` row must have: `symbol`, `direction_bias`,
   `confidence_score`, `valid_until`, `scanner_family`.
2. `valid_until` must be set **by the writer**, not defaulted to NULL.
3. Duplicate `(symbol, direction_bias)` within the cooldown window must be deduplicated
   **before** the `signal_pending` chain is triggered.
4. A crash mid-write must not leave orphan `open` opportunities that are never expired.

### Questions

**M1.** Run:
```sql
SELECT symbol, direction_bias, scanner_family, valid_until, confidence_score,
       created_at
FROM opportunities
WHERE status = 'open'
  AND (valid_until IS NULL OR scanner_family IS NULL OR confidence_score IS NULL)
LIMIT 20;
```
Are there NULL fields? If yes: which writer is responsible and what is the fix?

**M2.** In `market_observer.py`, find where `Opportunity` is constructed and
`insert_opportunity` is called. Is this inside a `try/except` that silently swallows
errors? If the DB write fails, does the observer retry, skip, or crash cleanly?

**M3.** Is the cooldown check in `market_observer.py` (`_recent_signal_pairs` or
equivalent) using the same `(symbol, direction)` key as the downstream
`_plan_signal_batch`? A mismatch means the observer can re-emit a signal that the paper
trader's cooldown then redundantly blocks, creating a false capacity exhaustion.

**M4.** What sets `scanner_family` on a written opportunity? Search for all callers of
`insert_opportunity`. Are there any that omit `scanner_family` or set it to a generic
default that would break learning attribution?

---

## DOMAIN 3 — Learning Outcome Attribution [third priority]

**Files:** `aletheia/learning/`, `aletheia/agents/paper_trader.py`,
`aletheia/storage/repositories.py` (outcome writes)

**Why high priority:** Survivorship bias here is severe. The loop **appears** to work
because verdicts get written and cohort stats accumulate. But if attribution is wrong,
the agent is systematically learning from noise — compounding over time.

### First-principles invariants

1. `learning_candidate_key` on a closed `trade_proposals` row must uniquely identify
   exactly one `(strategy_id, scanner_family, opportunity_id)` triple.
2. `forward_return_pct` must be computed from the **entry price of that specific trade**,
   not from an ambient snapshot price.
3. A verdict must **never** be written for a trade that has not reached terminal status
   (`closed` or `stopped`).
4. Stop-loss exit, take-profit exit, and time-stop exit must produce **distinct** outcome
   labels so the agent can learn from each independently.
5. `adopt_position` (exchange-reconciled positions) must produce the same learning signal
   as a normal paper-trader close.

### Questions

**L1.** Query:
```sql
SELECT COUNT(*) FROM trade_proposals
WHERE status = 'closed'
  AND (learning_candidate_key IS NULL OR strategy_id IS NULL OR scanner_family IS NULL);
```
If count > 0: which code path closes proposals without setting these fields?

**L2.** In `universe_forward_outcomes` (or equivalent table), is `entry_price` ever NULL
or 0 for rows with a non-NULL `forward_return_pct`? If yes: how is `forward_return_pct`
being calculated without a valid entry price?

**L3.** Find `insert_learning_verdict` (or equivalent). Is there a guard that checks
`trade_proposals.status IN ('closed', 'stopped')` before writing? Or can a verdict be
written for an `active` trade?

**L4.** When a position is closed via `adopt_position` (reconciler picks up an exchange
fill that we didn't originate), does the resulting `trade_proposals` row get a
`close_event` that triggers the learning loop? Or does it fall through silently?

**L5.** Is there a code path where two verdicts are written for the same
`learning_candidate_key` (e.g. duplicate calls, retry without dedup)? Check for unique
constraints on the verdict table and the insert logic.

---

## DOMAIN 4 — Guard System Over-Blocking [fourth priority]

**Files:** `aletheia/execution/guards.py` (or wherever guards live),
`aletheia/agents/paper_trader.py`

**Context from prior audit (2026-07-04):** The operator was skipping with `guard_blocked=21`
out of ~38 decisions in 3 hours. 12 momentum-long guards + 4 momentum-short guards active.
High-confidence candidates like BTC/USDT were being blocked. This may be correct — or
guards may have a TTL/expiry bug that lets stale guards accumulate.

### First-principles invariants

1. A guard must have a finite TTL. No guard should block indefinitely unless explicitly
   set to permanent.
2. Guards must be scoped to `(symbol, strategy_family)` — a loss on one family must not
   block an unrelated family on the same symbol.
3. A guard created from a false signal (e.g., a junk data event) must not survive a
   `valid_until` expiry of the source opportunity.

### Questions

**G1.** Find all places where guards are written. Is there a `valid_until` or `expires_at`
on each guard row? Run:
```sql
SELECT filter_name, COUNT(*), MIN(created_at), MAX(created_at),
       COUNT(CASE WHEN expires_at IS NULL THEN 1 END) as no_expiry
FROM guards  -- (use actual table name)
GROUP BY filter_name
ORDER BY COUNT(*) DESC;
```

**G2.** Is there a job or periodic task that **expires stale guards**? If not, guards
accumulate forever and the operator will increasingly skip everything.

**G3.** Is `scanner_family` used as part of the guard key? A loss on `mean_reversion`
family should not block a `momentum` family signal for the same symbol.

---

## DOMAIN 5 — Serenity Signal Integrity [fifth priority]

**Files:** anywhere `signal_source='serenity'` appears, serenity-related agents/repos

### Questions

**S1.** When both a `market_observer` signal and a `serenity` signal exist for the same
`(symbol, direction)`, which wins? Is this precedence logic tested? Find the code and
write a spec test if none exists.

**S2.** Trace `opportunity_id` from a `serenity` signal through to a closed
`trade_proposals` row. Is `signal_source='serenity'` preserved at every step, or does it
get overwritten to `'market_observer'` somewhere?

**S3.** What cleans up a `serenity` signal that expired without being traded? Is the
cleanup path the same as `market_observer` (TTL expiry job), or is there a separate path
that might be missing?

---

## DOMAIN 6 — Self-Monitoring Integrity [sixth priority]

**Context from Learning Loop Audit (2026-06-26):** `daily_review` was writing
"Strategy Factory IMPROVING" on loss days because it only checked agent-run success rate,
not P&L. If this hasn't been fixed, the operator is getting a corrupted feedback signal.

### Questions

**D1.** Find where `daily_review` (or equivalent) generates its "IMPROVING/STABLE/DECLINING"
assessment. Does it read from `closed_positions` P&L, or just from agent run status?

**D2.** Is there any monitoring dashboard or health check that shows a green status when
real P&L is negative? Find `compute_health` or equivalent and trace what inputs it uses.

---

## BUG FIX WORKFLOW (repeat for every confirmed bug)

```
1. Write a failing test first:
   - File: tests/specs/spec_<domain>.py  OR  tests/test_<domain>.py
   - The test must FAIL before your fix and PASS after.

2. Implement minimal fix (no refactoring beyond what's necessary).

3. Create branch and PR:
   git checkout -b fix/<slug>
   git add <specific files>
   git commit -m "fix(<domain>): <description>"
   gh issue create ...   # if no issue exists
   gh pr create ...      # with Fixes #<n> in body

4. Log progress:
   echo "FIXED: #<n> <domain>/<question-id> — <description>" >> /tmp/aletheia_sweep_progress.txt

5. Immediately continue to the next question. Do NOT stop.
```

---

## WHAT NOT TO DO

- Do NOT commit to `master` directly.
- Do NOT refactor code that isn't broken.
- Do NOT add error handling for scenarios that can't actually happen.
- Do NOT close a GitHub issue without a corresponding code change + test.
- Do NOT stop mid-domain. Exhaust all questions before moving on.
- Do NOT add comments that describe WHAT code does. Only add comments for non-obvious WHY.
- Do NOT run large-scale pytest on the local macOS device — use SSH to the CI runner
  instead (`AGENTS.md` has this rule). For single targeted tests, local is fine.

---

## STARTING SEQUENCE

```bash
# 1. Check progress file first
cat /tmp/aletheia_sweep_progress.txt 2>/dev/null || echo "(no prior progress — starting fresh)"

# 2. Read project rules
cat AGENTS.md | head -100

# 3. Confirm git state
git status && git log --oneline -5

# 4. Begin Domain 1, Question R1
# Read aletheia/execution/risk_engine.py (or grep for the file)
# Answer R1, then R2, then R3...
```
