# News and Learning Context Assessment

## Question

How do News, prior experience, Learning artifacts, and knowledge currently become
model-visible in Aletheia; where is this reliable; and what is missing compared
with frontier context-engineering practice?

This is a current-state assessment, not an authorization to change trading,
Risk, Execution, tool permissions, or live behavior.

## Current news path

```text
news providers
  → news_items
  → bounded News Analyzer LLM job
  → analyzed metadata (sentiment, relevance, symbols, impact summary)
  → Market Observer batch cache
  → up to three symbol-matched recent headlines in Market Observer context
  → structured Market Observer LLM call
  → persisted observer artifact / opportunity
  → System Operator receives the mediated upstream artifact, not raw news
```

Evidence in the current code:

- `news_fetch` writes news into `news_items`; the scheduled `news_analyze` job
  processes at most three rows independently.
- `aletheia/data/news_analyzer.py` passes a headline and a bounded 300-character
  summary to `llm_structured`, validates the structured response, and preserves
  retry/failed state rather than inventing analyzed facts.
- `MarketObserver` selects symbol-matched rows from a 48-hour batch window and
  renders at most three dated `title + source` lines into `## Recent News`.
  It records news item IDs, publication time, and source in its rendered source
  manifest.
- The current System Operator contract labels observer news evidence as
  **summary-mediated** rather than direct Operator evidence. It does not copy
  raw observer news/macro/on-chain/prediction identities into the operator
  prompt as direct facts.

This is already safer than indiscriminately appending an entire news feed. The
model-visible news text is narrow, symbol-specific, bounded, and tied to source
identities. However, the direct observer prompt currently carries headlines and
sources; it does not expose a uniform typed representation of publication age,
analysis state, relevance score, confidence, content provenance, or why an item
was selected.

## Current Learning and knowledge path

```text
outcomes / reviews / case builder
  → knowledge items, lessons, case studies, guard candidates
  → FTS retrieval plus guard matching
  → bounded advisory block
  → Market Observer structured LLM call
```

`knowledge.router.build_advisory_context()` retrieves role-relevant lessons and
case studies through FTS, optionally appends matching guard advice, then caps
the final block at 2,000 characters. The Market Observer builds one
symbol-agnostic run-level advisory block and applies symbol-specific guard
advice separately, so a generic memo/case text is not automatically presented
as a per-symbol fact.

Learning is advisory. Existing contracts preserve the separation between
Learning artifacts and runtime authority: guards and retrieval can guide model
reasoning, but cannot create an execution permission or bypass Risk approval.

The Agent Shell also has its own compacted operator-memory path. That is a
separate lifecycle context mechanism, not yet a common framework-wide
`ContextArtifact` interface.

## Strengths

1. **Bounded injection:** headline count, retrieval count, and character limits
   reduce context flooding.
2. **Causal evidence:** the Market Observer maintains rendered-source identity
   records and upstream artifacts preserve later decision lineage.
3. **Failure honesty:** news-analysis failure remains retry/failed rather than
   silently turning into a positive signal.
4. **Role-aware retrieval:** learning retrieval accepts an agent role and has a
   distinct guard-advisory path.
5. **Runtime authority remains outside text:** neither news nor lessons grant a
   tool or order permission.

## Frontier comparison

Current industry guidance generally recommends a stronger common abstraction:

| Capability | Current Aletheia | Frontier practice | Gap |
| --- | --- | --- | --- |
| Selection | Symbol matching, 48-hour news window, FTS lessons, bounded blocks | Hybrid lexical/semantic retrieval plus reranking and query-aware selection | No common selector/reranker or cross-source ranking policy |
| Provenance | News IDs/source manifests; durable knowledge records | Typed/versioned context artifacts with canonical references and consumed-version trace | No uniform per-call manifest across all agents |
| Freshness | News time window; subsystem-specific freshness rules | Volatile-segment freshness metadata with refresh policy before inference | No harness-wide freshness/expiry contract |
| Memory | FTS lessons/cases, guards, operator compaction | Canonical memory separated from derived summaries/embeddings/caches | No shared canonical-versus-derived context lifecycle |
| Security | Boundaries remain runtime-enforced; bounded text | Retrieval/memory treated as untrusted input; provenance and corpus-write governance | No unified untrusted-context labeling or injection evaluation |
| Evaluation | Unit/contract tests and a Header A/B study | Retrieval precision, grounding, freshness, task quality, and intervention impact measured together | No end-to-end context-injection evaluation suite |

Sources used for the comparison:

- Anthropic, [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic, [Contextual retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- OpenAI, [Retrieval guide](https://developers.openai.com/api/docs/guides/retrieval)
- LangChain, [Context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)
- Oracle, [Persistent memory and derived context](https://blogs.oracle.com/developers/persistent-memory-and-derived-context-a-two-layer-pattern-for-agents)
- OWASP-oriented memory/context poisoning analysis, [ASI06 overview](https://www.zealynx.io/blogs/owasp-asi06-memory-context-poisoning)

These references are design guidance and not proof that a given pattern will
improve Aletheia trading decisions. Context effects require Aletheia-specific,
paired evidence.

## Recommended sequence

### 1. Observe before broadening injection

Add a typed `ContextArtifact` manifest to the harness telemetry, not raw prompt
storage. Each selected segment should record: `kind`, stable ID, canonical
version/reference, source, observed/published time, selection reason, trust
class, and rendered character/token count.

### 2. Make News typed and freshness-aware

Keep the current bounded title injection initially. Add selection metadata to
its manifest, distinguish published time from analyzed time, and label news
content as untrusted external context. Do not promote LLM sentiment to a
trading fact without independent measurement.

### 3. Make Learning retrieval evidence-aware

Retain FTS as a lexical baseline. Add a retrieval evaluation corpus before
introducing embeddings/reranking: measure relevance, stale/contradictory
retrieval, grounded use, and downstream decision quality. Canonical lesson/case
records must remain authoritative over summaries and future vector indexes.

### 4. Evaluate context as an intervention

For each caller class, compare no-context, current-context, and one changed
context policy under frozen fixtures. Measure token cost, schema validity,
retry/fallback rate, grounding/provenance use, and task-quality rubric. For
trading-related callers, preserve the existing causal evidence chain and keep
Risk/Execution outside the experiment.

## Learning A baseline — 2026-08-05

A fixed, local SQLite/FTS fixture now records the current retrieval baseline;
it does not exercise a provider or change production retrieval.

| Measure | Result | Meaning |
| --- | --- | --- |
| Recall@3 | 1.00 | Each of three expected high-confidence active items was retrieved. |
| Precision@3 | 0.75 | One extra active item shared generic advisory vocabulary. |
| Stale/conflict recall | 0 | An archived historical note did not return. |
| Empty-query false retrieval | 0 | An unrelated query returned no item. |

This is a baseline, not a pass/fail quality threshold. Its purpose is to make
any future lexical-query, metadata-filter, hybrid retrieval, or reranking
change comparable to the current FTS behavior.

## Learning B — context-effectiveness protocol

Learning B asks a different question: whether an **authorized** Learning bundle
improves a Market Observer model output relative to the same frozen context
without that bundle. The treatment boundary is already explicit:
`authorize_observer_learning_bundle()` renders the bundle only after the
paper-authority experiment assigns the treatment; the control renders an empty
bundle. Both paths preserve the same non-Learning market context and runtime
permissions.

The protocol will retain only aggregate facts for each paired call:

- treatment/control assignment and bundle fingerprint;
- ContextArtifact kinds and rendered character counts;
- provider-reported input/output usage, schema-validity, retries, and fallback;
- a blinded owner quality rubric and grounded-use outcome.

It must not persist prompt text, model responses, news bodies, or Lesson bodies.

### Current status

The mechanical treatment/control boundary, exact logical-call linkage,
prompt-free ContextArtifact observation, strict output-schema validation, and
fallback-disabled paired runner are implemented. Provider Settings exists, but
the current CASI instance still has no configured usable provider profile and
credential binding. A fake model can prove only plumbing; it cannot prove that
Learning improves reasoning. Therefore no grounded-use or owner-quality
improvement is claimed yet.

Run one real pair after a provider is configured:

```text
aletheia audit learning-context-b1 \
  --review-output <ephemeral-path-outside-the-repository> \
  --json
```

The runner uses the frozen
[`learning_b_market_observer_v1.json`](fixtures/learning_b_market_observer_v1.json)
fixture, the same Market Observer schema and token cap in both arms, one exact
provider/model, no model fallback, randomized arm order, and content-free
ledger records. Raw outputs exist only in the explicit mode-0600 blind-review
packet. After the owner fixes A/B scores, import an aggregate score JSON with
`learning-context-b1-score`; delete the raw packet afterward.

```json
{
  "reviewer": "owner",
  "rubric_version": "learning-b-owner-v1",
  "scores": {
    "A": {"quality_score": 1, "grounded_use": "not_applicable", "context_harm": false},
    "B": {"quality_score": 1, "grounded_use": "yes", "context_harm": false}
  }
}
```

`quality_score` is an integer from 1 to 5. The score import resolves blinded
labels from content-free call metadata and persists only per-arm aggregates.

When CASI is ready, run paired calls against frozen representative fixtures,
then record aggregate outcomes only. Keep the experiment paper-only and leave
Risk, Execution, permissions, and live trading outside its scope.

## Explicit non-goals

- Do not inject a full news feed, full historical memory, or raw operator logs.
- Do not use News or Learning text as a permission system.
- Do not store production raw prompts in the economics ledger.
- Do not enable a vector database or automatic learning influence merely because
  frontier systems use retrieval; first establish a measured deficiency in the
  current bounded FTS path.
