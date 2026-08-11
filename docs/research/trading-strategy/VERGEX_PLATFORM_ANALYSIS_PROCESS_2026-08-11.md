# Vergex Platform Analysis Process Evidence

Date: 2026-08-11

Role: point-in-time research evidence

Repository snapshot: `4309bda0`

Source folder: `/Users/yixian/Desktop/vergex-security-report/`

## Scope and authority

This document records how the Vergex report bundle was examined and how its
technical ideas were compared with Aletheia. It does not authorize importing
Vergex data, changing an Aletheia prompt, adding a data source, modifying Risk
or Execution, or implementing any candidate described by the companion report.

The requested objective was to learn from Vergex's technology and product
ideas, then identify which capabilities Aletheia already has and which it does
not. The report bundle was treated as research evidence, not as an operational
data feed or a source of current runtime facts.

Sensitive evidence in the source folder includes account, wallet, order,
credential, prompt, and other-user material. None of those values are copied
into this document. Proof-of-concept scripts were inspected statically and were
not executed.

## Questions examined

The analysis answered five separate questions:

1. What durable technical ideas can be learned from the bundle?
2. Which of those ideas have corresponding code in current Aletheia?
3. Which code paths are integrated consumers rather than isolated helpers?
4. Which capabilities are absent, approximate, or intentionally outside scope?
5. What definitions and evidence gates must be frozen before implementation is
   considered?

This separation prevents four common classification errors:

- a report description being mistaken for a verified algorithm;
- an isolated helper being mistaken for a production capability;
- a wired code path being mistaken for current Fedora runtime evidence;
- a Vergex SaaS feature being mistaken for an Aletheia product requirement.

## Evidence inventory

The source directory contained the same four artifact families recorded by the
earlier Position X research:

| Artifact family | Count | Use in this analysis |
|---|---:|---|
| Markdown reports | 15 | Threat findings, API surface, data-source reasoning, formula reconstruction, moat assessment |
| Python scripts | 14 | Static evidence of extraction and reconstruction methods; not executed |
| JSON files | 66 | Schema and formula-shape evidence; sensitive values not reproduced |
| JavaScript fragment | 1 | Frontend/service surface discovery |

The bundle was grouped by purpose rather than read as one homogeneous product:

| Evidence family | Representative material | Analytical use |
|---|---|---|
| Security findings | findings, API map, JWT analysis, final breach report | Identify exposed trust boundaries and distinguish security failures from product capability |
| Structure intelligence | signals, structure overviews, analysis samples | Recover the observable input/output vocabulary |
| Formula reconstruction | composite fit, dimension fit, POC and fuel scripts | Determine what was actually reconstructed and what remains inferred |
| Holder and position evidence | holder samples and partial address inventory | Identify raw inputs required by cost/liquidation structure |
| Product thin layers | marketplace, model, billing, proxy, agent and wallet surfaces | Separate reusable capability from multi-user SaaS packaging |
| Public market data | candles, asset catalogs, funding/OI context | Avoid counting already-public inputs as proprietary capability |

## Repository authorities inspected

The comparison used current code and current repository authorities in the
following order:

1. `docs/product/SCOPE.md` for the local-first, single-owner product boundary.
2. `docs/product/CAPABILITY_GROUND_TRUTH.md` for support classifications.
3. `docs/design/data/data_utilization.md` for actual producer, storage,
   consumer, and status claims.
4. `docs/design/contracts/agent_contracts.md` for Position X authority and
   Agent consumption.
5. `docs/design/architecture/SYSTEM_MAP.md` for the current scheduled topology.
6. Current Python implementation and focused tests for algorithm and consumer
   evidence.
7. `docs/research/trading-strategy/position_x_deep_research.md` as historical
   research context, not current authority.

Current code inspected included:

- `aletheia/data/position_x.py`;
- `aletheia/data/collectors/vergex_structure.py`;
- `aletheia/data/perp_structure_shadow.py`;
- `aletheia/agents/market_observer.py`;
- `aletheia/agent_shell/trading_context.py`;
- `aletheia/agent_shell/trading_decision.py`;
- storage migrations and repository functions for structure snapshots and
  shadow outcomes;
- focused Position X, data-utilization, Doctor, context, and settlement tests.

## Method

### 1. Static source-bundle review

The bundle was inventoried by file type, size, and declared purpose. Markdown
headings and tables were extracted to locate the principal claims. JSON shape
was inspected without reproducing secrets or account-level values. Script
entry points and formulas were read statically.

### 2. Capability decomposition

Vergex was decomposed into independently assessable layers:

1. raw market and holder inputs;
2. cost and liquidation distributions;
3. derived metrics;
4. cross-sectional dimensions and composite score;
5. history and evaluation;
6. Agent explanation and decision consumption;
7. UI/product surfaces;
8. multi-user commercial and security infrastructure.

This matters because Aletheia can possess a derived-metric helper while lacking
the raw position inventory, or possess outcome infrastructure while lacking a
continuous structure-signal cohort.

### 3. Evidence-level classification

Every compared capability was assigned one of these states:

| State | Meaning |
|---|---|
| Implemented | Current code plus a real consumer or durable result exists |
| Partial | A helper or bounded path exists, but semantics, fidelity, producer, consumer, or proof is incomplete |
| Not implemented | No matching current code or consumer was found |
| Intentionally excluded | Capability conflicts with current product scope or safety boundary |
| Runtime not verified | Code exists, but current Fedora data/config/health was not queried in this analysis |

### 4. Multi-temporal comparison

Claims were separated into four time views:

| Time view | Authority |
|---|---|
| Vergex report time | What the July 2026 bundle showed about Vergex |
| Historical Aletheia plan | What the July Position X research proposed |
| Current repository | What commit `4309bda0` implements and wires today |
| Candidate future | What could be considered only after owner review |

No current-runtime claim was inferred from repository documents. Fedora remains
the authority for live provider, schema, job, and observation-window state.

### 5. Algorithm-fidelity review

Function names were not accepted as proof of semantic equivalence. Each metric
was traced from required inputs through the actual calculation. The review
specifically checked:

- whether concentration ranked holders or price buckets;
- whether cost and liquidation walls were clustered or represented by one
  nearest observation;
- whether Value Area was contiguous;
- whether signed PnL preserved the intended CGO direction;
- whether liquidation concentration used stable price bins;
- whether percentile helpers had a scheduled cross-sectional producer;
- whether the composite score was a research prior or a locally validated
  model.

### 6. Product-fit review

Marketplace, copy-trading, wallet, billing, user-Agent, and code-strategy
surfaces were evaluated separately from structure intelligence. Their absence
does not automatically constitute an Aletheia gap because the current product
is local-first and single-owner, and arbitrary code execution or agent-owned
credentials are explicitly excluded.

## Verification performed

The analysis used read-only repository and source inspection. One local Python
invocation exercised the existing safe material extractor to confirm that it
could derive non-sensitive knowledge-card metadata. That invocation did not
persist runtime state, call an external endpoint, execute the bundle's PoC
scripts, or change trading behavior.

Tracked-worktree status was clean after inspection. No current Fedora runtime,
database, provider, scheduler, or observation-window status was asserted.

## Approved implementation follow-through

After the read-only analysis, the owner separately approved P0, P1/P2, and the
read-only P3 evaluation capability in this worktree. P0 froze the offline
formulas and availability semantics. P1
added the deterministic multi-market benchmark, declared parameter-sensitivity
checks, average-rank ties, explicit availability strata, and competing composite
baselines. P2 extended the existing explicit CLI to accept a sanitized holder
batch and persist only aggregate snapshots in one transaction. P3 adds strict
prospective eligibility and multi-baseline gross evaluation over settled
outcomes while leaving cost-aware evidence unavailable. These later changes do
not alter the source-bundle inspection record above and do not claim Fedora
deployment, an opened observation window, or runtime readiness.

Implementation verification comprised 37 focused tests, 58 relevant contract
and documentation-spec tests, Ruff on all changed Python files, and
`git diff --check`; all passed.

## Material limitations

- The Vergex formulas were reconstructed from exposed samples; they are not an
  authoritative vendor specification.
- Public Hyperliquid fields do not by themselves provide a complete historical
  holder universe. Coverage quality remains a central dependency.
- The current Aletheia implementation includes intentionally minimal offline
  functions. Their presence does not prove numerical equivalence with Vergex.
- No forward economic evaluation of the Position X dimensions was performed in
  this documentation task.
- The source bundle is a July 2026 snapshot. It does not establish Vergex's
  present external production state.

## Result handoff

The complete comparison, candidate repairs, feature-extraction priorities,
metric definitions, benchmarks, decision gates, and implementation review
questions are recorded in
[Vergex Platform Gap and Implementation Candidate Report](VERGEX_PLATFORM_GAP_AND_IMPL_CANDIDATE_REPORT_2026-08-11.md).

That report remains the research and decision record. It now distinguishes the
approved P0-P3 code evidence from the still-unopened prospective cohort and
unapproved P4 candidates.
