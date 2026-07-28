# Serenity Lens 24h Audit - 2026-07-03

## Scope

Branch: `audit/serenity-lens-24h`

This is a read-only audit of D11: Serenity Lens 24h research capability and
closed-loop readiness. It does not add research scope, create strategies, place
orders, change risk behavior, or modify Serenity runtime code.

## Implementation Chain

The implemented chain is present:

- Scheduler: `dreaming_daily` at 02:00 Asia/Shanghai enqueues a Serenity research
  run before lesson extraction.
- Worker: `aletheia.agents.research_worker` persists run events, claims,
  evidence, themes, and opportunities.
- Doctor: `data.serenity_pipeline` reports last Serenity run age and active
  opportunity count, warning when the last run is older than 26h.
- API/UI: Serenity Lens exposes status, runs, themes, evidence, profile,
  deep-dive, promote, theme detail, claims, and run artifacts.
- Promote path: `POST /api/serenity/themes/{theme_id}/promote` writes
  `PiDecision(action='signal_pending', signal_source='serenity',
  opportunity_id=<serenity_opportunity_id>)` for active opportunities.
- Learning/trading context: tests cover Serenity pending-signal matching,
  opportunity attribution, researchability, verdicts, xStock eligibility, and
  post-trade feedback hooks.

## Production Snapshot

Read-only sample from `data/app.db`:

- DB size: 448,491,520 bytes
- Serenity tables present: 14
- `serenity_research_runs`: 36
- `serenity_run_events`: 346
- `serenity_themes`: 132
- `serenity_claims`: 589
- `serenity_theme_claims`: 604
- `serenity_evidence`: 524
- `serenity_opportunities`: 75
- `serenity_post_mortems`: 0
- `serenity_risk_gates`: 0
- `serenity_feedback_events`: 0
- `serenity_user_profiles`: 0

Latest run:

- id: `d5ff0849944e`
- status: `completed`
- trigger: `scheduled`
- created: `2026-07-02T18:00:00.011684+00:00`
- finished: `2026-07-02T18:02:27.189010+00:00`
- artifacts: `themes=4`, `evidence=44`, `claims=20`, `opportunities=3`

Last 24h production activity:

- research runs: 2
- completed research runs: 2
- evidence rows: 56
- claims: 40
- Serenity -> PI decisions: 5

Distribution:

- theme status: `active=132`
- opportunity status: `active=75`
- opportunity asset/market: `crypto spot=75`
- evidence sources include market data, news, ICT/SMC methodology, research,
  price action, macro, derivatives data, on-chain, and social.

## API Snapshot

Live `:8787` read-only API sample:

| Path | Status | ms | Bytes |
|---|---:|---:|---:|
| `/api/serenity/status` | 200 | 75.6 | 930 |
| `/api/serenity/pipeline` | 200 | 6.9 | 18,058 |
| `/api/serenity/runs?limit=5` | 200 | 3.6 | 1,615 |
| `/api/serenity/themes?status=active` | 200 | 15.4 | 136,463 |
| `/api/serenity/evidence?limit=20` | 200 | 5.3 | 23,158 |
| `/api/serenity/profile` | 200 | 4.8 | 16 |
| `/_infra/doctor` | 200 | 1,519.3 | 8,287 |

## Quantified Outcome

- D11 implementation status: mostly proven.
- 24h research freshness: proven by 2 completed runs in the last 24h.
- Research artifact production: proven by 56 evidence rows and 40 claims in the
  last 24h.
- Promote/PI handoff: proven by 5 Serenity-sourced PI decisions in the last 24h.
- UI/API visibility: 7/7 sampled endpoints returned 200.
- Feedback closure is not production-proven yet: 0 post-mortems, 0 risk gates,
  0 user feedback rows, and 0 profiles in the sampled DB.

## Verification

- `uv run pytest tests/test_serenity_workbench.py tests/test_serenity_routes.py tests/test_serenity_scoring.py -q`
  - `67 passed`
- `uv run pytest tests/test_serenity_p0_p3_e2e.py tests/specs/spec_serenity_promote.py tests/specs/spec_serenity_evidence.py -q`
  - `48 passed, 1 warning`
- `uv run pytest tests/specs/spec_serenity_multi_lens.py tests/specs/spec_serenity_researchability.py tests/specs/spec_serenity_verdict.py tests/specs/spec_serenity_attribution.py tests/specs/spec_serenity_live_readiness.py tests/specs/spec_serenity_xstock_execution.py -q`
  - `35 passed`

## Remaining Risk

No code fix is recommended for D11 in this pass. The remaining gap is evidence
collection over time:

- The research and promote chain is active.
- The post-trade feedback/post-mortem chain needs real closed Serenity trades to
  prove production behavior.
- Risk-gate rows are structurally supported but absent in production data.
- User profile and feedback rows are empty because the sampled instance has not
  used those controls.
