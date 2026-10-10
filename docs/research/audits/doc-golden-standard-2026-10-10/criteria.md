# Doc audit criteria (shared by all reviewers)

Repo worktree: /Users/yixian/Projects/aletheia-worktrees/docs-spec-cleanup (read-only for you; DO NOT edit files).

Baseline ("golden standard") = the SDD spec format in docs/design/specs/README.md and the
approved spec docs/design/specs/0001-learning-loop/{spec,design}.md. Read those first.
Also read docs/product/CAPABILITY_GROUND_TRUTH.md, docs/product/ACTIVE_RUNTIME_SCOPE.md and
docs/governance/policies/documentation_governance.md (six document roles; Contract = current
behavior only; targets go to Proposal/Spec; no progress state in docs).

Current product truth to check every doc against:
- Slim Core + Bitget Exchange Extension (UTA v3 target protocol; Bitget Demo is the qualification
  route) + HTTP model endpoints. OKX/Binance frozen. Spot OUT of scope (owner 2026-10-09);
  USDT-margined crypto perpetuals only; RWA perps blacklisted.
- Local paper simulator / paper_internal RETIRED. Python Operator assessment loop RETIRED; the
  TS operator-shell is the sole Operator. Ranker/PaperTrader and Market Observer RETIRED.
  CLI/OAuth model providers retired (HTTP only).
- Entry mainline: Universe → Candidate → Planning Generation → Entry Planner (LLM hypothesis)
  → deterministic Compiler → immutable Entry Plan → Proposal → Risk Engine → Execution Gateway
  → UTA → Order/Fill/Position → verified close → cost-complete Outcome.
- Owner goal: AI trades like a discretionary human trader (review → lesson → next decision),
  not a quant factor miner. Futures product, ship fast, no over-engineering, one capability =
  one implementation (delete the loser, no shims).
- Learning/self-evolution is redesigned by spec 0001 (Playbook, judgment R labels, advisory vs
  authority tier). OUT OF YOUR SCOPE: learning_contract.md, knowledge_contract.md,
  counterfactual_contract.md, learning proposals, research/counterfactual, research/agents-learning.
  But DO flag where your docs conflict with spec 0001 (e.g. they describe ps_lessons / guards /
  memos / lesson_reviewer as current mainline consumers, or entry-plan fields LL-R01 needs).

For EACH document assigned to you, read it fully and judge:
1. Role: is it a single role (Product/Contract/Decision/Spec-Proposal/Operations/Evidence)? Mixed?
2. Truth: does it describe retired mechanisms (paper simulator, OKX/Binance as active, spot,
   Python operator loop, ranker, market observer, CLI providers, equity/prediction observers...)
   as current? Spot-check 3-6 concrete code claims (file paths, function names, table names,
   job IDs, config keys) with grep/Read against the repo; report which exist and which don't.
3. Specificity: are behaviors stated as checkable requirements (trigger → response, failure
   semantics, unavailable-vs-zero, authority) or vague prose? Does it give entry point,
   durable state, consumer, invariants, failure semantics?
4. Rationale ("有道理吗"): does the component/function make sense for the product goal above?
   Is it on the trading mainline, a justified support function, or a fossil/optional surface
   that should be frozen/archived/deleted? Duplicate of another doc/implementation?
5. Bloat: history/changelog prose, PR-by-PR narrative, issue numbers as content, repeated
   disclaimers, copied facts owned elsewhere.
6. Verdict, one of: KEEP (minor edits) | REWRITE (same role, restructure to spec-like
   requirement form) | SPLIT (current part stays, target part → spec, history → history/) |
   MERGE into <doc> | CONVERT to spec (target design not yet current) | ARCHIVE (move to
   design/history or archive) | DELETE.
7. Concrete edits: list the specific sections/lines to change and what the new text must say
   (enough that another agent can apply it without re-deriving).

Return a Markdown report (in Chinese prose, keep identifiers in English) with:
- A summary table: doc | lines | role | verdict | top problem (one line).
- Per doc: findings with evidence (path:line for both doc and code), rationale judgment, and the
  concrete edit list.
- A short "cross-doc" section: contradictions between your docs and other docs, duplicates,
  missing contracts (mainline behavior that has code but no contract), and conflicts with spec 0001.
Be evidence-driven: a claim that code is missing must come from an actual grep, not a guess.
Write your full report to /tmp/docaudit/<your-domain>.md AND return it as your final text.

## Persistence rule (mandatory)
Sessions get interrupted. After finishing EACH document, immediately APPEND that document's
section to your report file (use `cat >> file <<'X' ... X`). Before starting, read your report
file if it exists and skip documents already covered. Keep each per-doc section ≤ 60 lines.
Write the summary table last, at the end of the file.
