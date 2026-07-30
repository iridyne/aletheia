# Existing `aletheia audit` CLI Shape — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics`
branch. Purpose: enumerate the existing CLI subcommands, the module
functions they call, and the JSON shapes they emit, so a Phase 1
read-only `/api/economics/*` surface can mirror them exactly and both
routes stay in step with a single implementation.

Sources: `aletheia/cli/audit_cmd.py`,
`aletheia/economics/tracing.py`, `aletheia/economics/viability.py`.

## Command surface

| Command | Purpose | Backing function | Output |
|---|---|---|---|
| `aletheia audit verify` | Verify `audit.jsonl` hash chain. | `aletheia.files.jsonl.verify_audit_chain()` | Text; exit 0 if valid, exit 1 with up to 20 first errors. Not an economics surface — grouped under `audit` for historical reasons. |
| `aletheia audit data` | OHLCV coverage + 7-day signal effectiveness across active universe members. | Inline SQL over `market_ohlcv` and `universe_forward_outcomes`. | Text table by default; `--json` emits `{generated_at, min_bars_for_backtest, total_active_members, symbol_coverage[], signal_effectiveness_7d}`. Not an economics surface. |
| `aletheia audit pricing-set` | Add an immutable model-price snapshot. | `economics.tracing.add_pricing_snapshot` | JSON of the inserted row. |
| `aletheia audit tool-pricing-set` | Add an immutable tool cost profile. | `economics.tracing.add_tool_cost_profile` | JSON of the inserted row. |
| `aletheia audit costs` | List and aggregate `runtime_cost_events` in a window. | `economics.tracing.list_cost_summary` | Text summary by default; `--json` emits full dict below. |
| `aletheia audit viability` | Deterministic System or Operator viability audit. | `economics.viability.build_system_viability` / `build_operator_viability` + `render_viability`. | Text renderer by default; `--json` emits full report dict. |

The three commands relevant to Phase 1's read-only surface are
`pricing-set` (write), `tool-pricing-set` (write), `costs` (read), and
`viability` (read + persist). Everything else is unrelated.

## Full JSON shapes

### `list_cost_summary` (backing `aletheia audit costs --json`)

Time window is **inclusive on both ends**: `started_at >= start AND
started_at <= end` (SQL at `tracing.py:586-587`, PR #462 fix). The
comparison replaces `' '` with `'T'` on both sides so a `2026-07-30
12:34:56` row still matches a `2026-07-30T…` filter — the ledger
normalises ISO shapes at read time.

```json
{
  "window_start": "2026-07-23T12:00:00+00:00",
  "window_end": "2026-07-30T12:00:00+00:00",
  "event_count": 42,
  "known_cost_subtotal_usd": 0.12345678,
  "unknown_event_count": 3,
  "by_type": {
    "llm":   {"event_count": 30, "known_cost_usd": 0.098765,  "unknown_count": 2},
    "tool":  {"event_count": 10, "known_cost_usd": 0.024691,  "unknown_count": 1},
    "data":  {"event_count": 2,  "known_cost_usd": 0.000000,  "unknown_count": 0}
  },
  "events": [
    { "id": "...", "event_type": "llm", "run_id": "...", "runtime_session_id": "...",
      "opportunity_id": null, "decision_id": null, "proposal_id": null,
      "call_id": "llm_...", "provider": "openai_native", "model_or_tool": "gpt-5",
      "requested_model": "gpt-5", "attempt_index": 0, "status": "success",
      "started_at": "...", "finished_at": "...", "latency_ms": 1234.5,
      "usage_json": "{...}", "pricing_snapshot_id": "...", "cost_profile_id": null,
      "estimated_cost_usd": 0.001234, "currency": "USD", "cost_status": "known",
      "error_type": null, "metadata_json": "{...}", "created_at": "..." },
    ...
  ]
}
```

Notes for the API layer:

- `events` grows unbounded with the window; a real endpoint should
  paginate or expose a `?include_events=false` toggle. The CLI's text
  mode already suppresses the `events` array.
- `event_type` values are the schema check-constrained enum:
  `'llm' | 'tool' | 'data' | 'infrastructure' | 'other'`.
- `cost_status` values are `'known' | 'estimated' | 'unknown'`.
- `estimated_cost_usd IS NULL OR cost_status='unknown'` both count as
  "unknown" for the summary — the summary is defensive about either
  representation of a gap.

### `build_system_viability` (backing `aletheia audit viability --scope system --json`)

Requires: `--data-cost`, `--infrastructure-cost`, `--other-cost`
(each `>= 0`, or omitted → `insufficient_data`/`invalid` verdict).
`--as-of` pins the window end for reproducible reruns; otherwise
the audit uses `now(UTC)`.

Returned dict mirrors the `viability_reports` table (see
`aletheia/economics/schema.py:92-118`), plus a report identity:

```json
{
  "id": "vp_...", "dedup_key": "sha256:...",
  "scope": "system",
  "window_start": "...", "window_end": "...",
  "execution_mode": "paper_internal",
  "metric_version": "aletheia-viability-v1",
  "outcome_contract_version": "...",
  "net_trading_result_usd": 3.0,
  "baseline_result_usd": null,
  "incremental_value_usd": null,
  "intelligence_cost_usd": 0.5,
  "tool_cost_usd": 0.1,
  "data_cost_usd": 0.4,
  "infrastructure_cost_usd": 0.0,
  "other_cost_usd": 0.0,
  "deployment_cost_usd": 1.0,
  "viability_amount_usd": 2.0,
  "verdict": "positive",
  "cost_completeness": "complete",
  "sample_count": 5,
  "exclusion_counts_json": "{}",
  "assumptions_json": "[...]",
  "reason": "...",
  "generated_at": "..."
}
```

`verdict` values: `positive | neutral | negative | insufficient_data
| invalid`.

`build_operator_viability` returns the same top-level shape with
`scope="operator"`, `baseline_result_usd` and `incremental_value_usd`
populated, and no infrastructure/other cost fields consulted.

Both builders **persist** to `viability_reports` when
`persist=True` (default) — this is a write side effect the CLI
inherits. A future GET endpoint that only wants to view historical
reports without recomputing should query `viability_reports` directly
rather than call the builders. This is a real distinction: the
CLI conflates "run an audit" with "view historical audit", and a
Phase 1 API might want to separate them.

## Kill switches consulted

- `list_cost_summary` does not check `tracing_enabled()` — it reads
  whatever is in the ledger regardless. Writes (`record_llm_attempt`,
  the SQL trigger for tool calls) do respect
  `ALETHEIA_COST_TRACING=0`.
- Both viability builders check `audit_enabled()` and raise
  `RuntimeError("viability audit is disabled by
  ALETHEIA_VIABILITY_AUDIT")` when disabled. The API layer should
  translate this to `503 Service Unavailable` with an explicit reason
  rather than a 500.

## Suggested Phase 1 endpoint layout

Straight mirror of the CLI, no logic duplication — each route calls the
same `economics.tracing` / `economics.viability` functions:

| Method | Path | Backing call | Purpose |
|---|---|---|---|
| `GET` | `/api/economics/costs?days=&run_id=&include_events=` | `list_cost_summary` | Windowed spend breakdown with `unknown_event_count` gap and optional row-level events. |
| `GET` | `/api/economics/viability?scope=&days=&data_cost=&as_of=` | `build_*_viability(persist=False)` | Compute a viability report; persist optional via a separate `POST` or a `?persist=true` toggle. Default `persist=false` for a GET. |
| `GET` | `/api/economics/viability/reports?scope=&limit=&offset=` | direct query of `viability_reports` | Historical audits without recomputing. |
| `GET` | `/api/economics/broker` | `inference_broker.snapshot()` | Live per-provider queue/RPM/TPM/active metrics. |
| `GET` | `/api/economics/model-health` | `fallback.get_model_health_snapshot()` | Per-`(provider, model)` cooldown state. |
| `GET` | `/api/economics/pricing` | direct query of `pricing_snapshots`, `tool_cost_profiles` | List active price contracts. |

Writers (`pricing-set`, `tool-pricing-set`) are intentionally kept
CLI-only for Phase 1: they mutate the accounting contract and the
CLI already has the identity semantics right. Exposing them as HTTP
mutations without an auth-and-audit story is not appropriate for
Phase 1.

## Small implementation notes

- The `list_cost_summary` "events" array is emitted verbatim by the
  CLI's `--json` mode. A real HTTP endpoint should cap it (e.g.,
  `?event_limit=200` default) and expose a follow-up cursor if we
  ever need the full list. Otherwise a 30-day window over a busy
  runtime could produce a several-megabyte JSON blob.
- `record_llm_attempt` reads `_current_run_id()` from
  `run_context` at write time — this is one-way. The API summary
  reads whatever was stamped then. No back-fill or re-attribution.
- Column names on the ledger row use SQL underscore form
  (`estimated_cost_usd`, `pricing_snapshot_id`). The API should keep
  them as-is; a camelCase transform would create a second contract
  to maintain.
- Both viability builders sit behind `audit_enabled()`. The Phase 1
  API should expose `audit_enabled()` state on a small
  `/api/economics/status` endpoint so the frontend can render a
  "viability audit disabled by kill switch" banner rather than a
  generic error.
