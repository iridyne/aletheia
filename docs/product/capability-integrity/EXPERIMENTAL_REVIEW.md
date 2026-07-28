# Experimental Capability Integrity Review

This is the read-only review of the 20 capabilities classified
`Experimental` before this pass. The review asks whether each public promise
reaches its stated final result; it does not require natural runtime evidence
and does not promote an experiment merely because tests pass.

Initial read-only result:

- 19 remain honest, bounded `Experimental` capabilities.
- 1 composite capability, DC-13, is reclassified `Partial`.
- 1 new product-integrity issue was created: GitHub #429.
- No scheduler, runtime state, or test framework was added.

Follow-up: DC-13 is now again `Experimental` because the existing UI explicitly
states that More/Less records history only and does not change future research.
The behavioral consumer was not invented merely to make the button appear more
capable.

Follow-up: Persona progressive unlock is the 21st Experimental capability.
Only production-consumed entry parameters can now unlock, while Settings
explicitly states that the current gate uses global historical closes rather
than a fresh Persona-specific evidence window. GitHub #409 tracks that gap.

## Results

| ID | Capability | Full-chain conclusion | Verdict |
|---|---|---|---|
| RT-09 | Runtime continuity candidate/shadow/promotion | CLI/API reach candidate materialization, shadow checks, promotion, drain, and rollback. Settings labels it Experimental. No pinned natural promotion is claimed. | Experimental |
| MD-07 | Universe family calibration | Calibration produces a bounded family multiplier with neutral fallback. It remains an explicit experiment; external Windows observations are tracked separately from this local code review. | Experimental |
| MD-11 | Equity opportunity observation | Market-hours scheduler reaches the equity observer and durable, non-execution-eligible opportunities. Settings explicitly says observation-only. | Experimental |
| MD-12 | Prediction-market observation | Observer data reaches the Prediction Lens/research context and is explicitly excluded from Paper/Live execution. | Experimental |
| DC-11 | Operator sessions, tasks, and extensions | Session/task state is durable. Enabled slash extensions proxy only registered, permission-gated typed commands; they do not create new execution authority. | Experimental |
| DC-12 | Serenity multi-lens research | Research runs consume profile snapshots, produce themes/evidence/opportunities, and expose failures/results. Promotion remains research-scoped and outside the supported trading path. | Experimental |
| DC-13 | Serenity preferences and theme feedback | Profile preferences reach the research worker. More/Less reaches `serenity_feedback_events` and detail history, and the UI explicitly labels that non-behavioral boundary. | Experimental — #429 resolved by default-branch delivery |
| RK-08 | Go-live eligibility scorecard | Scorecard and unlock prerequisites are executable, but live execution is intentionally outside support. The separate new-proposal routing defect remains EX-04/#418. | Experimental |
| EX-02 | Exchange demo trading | Existing adapters, Risk, materialization, and reconciliation paths are tested. No exchange demo mode is part of the owner-declared supported profile. | Experimental |
| EX-03 | Live Unlock/relock | Unlock/relock authority state is durable and visible. It is not represented as a supported live pipeline here; EX-04/#418 records the broken proposal-mode join. | Experimental |
| EX-07 | Exchange-native TP/SL brackets | Broker-specific private-fill/bracket paths and failure handling exist, while real-venue behavior remains outside the support promise. | Experimental |
| EX-11 | External demo-position adoption | Opt-in reconciliation classifies an external demo position, creates attributable adoption records, and places it under existing manager/reconciler handling. Live positions and disabled adoption fail closed. | Experimental |
| PF-09 | Alpha/family/factor attribution | Cost-aware analyzers produce owner-visible verdicts and explicit insufficient-data states. No natural cohort result is claimed as Alpha proof. | Experimental |
| LN-07 | Skill drafting and review | Eligible lessons create permission-bounded draft artifacts. Automatic activation is intentionally absent; the capability promise is draft/review, not autonomous skill execution. | Experimental |
| LN-10 | Shadow trades/counterfactual resolution | Non-selected candidates reach durable shadow rows, cost-aware resolution, and analytics. The output is observation evidence, never execution authority. | Experimental |
| LN-11 | Filter controller observation | The controller produces read-only recommendations and explicitly does not mutate filter parameters. UI/docs match that boundary. | Experimental |
| LN-12 | Measured Learning contribution | Exposure/outcome cohorts reach evaluator verdicts and the Learning read model, including invalid/insufficient states. No natural treatment/control verdict is claimed. | Experimental |
| LN-13 | Learning lifecycle automation | The scheduler reaches a paper-only, default-off, health-gated advance/refreeze controller. It cannot obtain live authority. | Experimental |
| LN-15 | Persona regime learning | Clean regime cohorts can produce a bounded appetite adjustment that reaches notional sizing; missing/dirty/small cohorts return zero. | Experimental |
| LN-16 | Persona progressive unlock | Runtime filters the ladder to active entry notional/Kelly parameters and distress refreezes them. The gate still reuses lifetime eligible closes across Persona/policy epochs, so it remains default-off and explicitly non-supported. | Experimental — #409 |
| LN-18 | Persona rollout automation | The 8-hour job is now a read-only safety hold. It preserves integrity/distress reasons but cannot treat elapsed time as a Persona/policy step verdict or mutate config. Settings blocks new enablement and allows legacy configurations to switch off. | Experimental — #409 |

## Evidence used

Static review followed the real entry, durable state, consumer, result, and
failure boundary for every row. Two focused executable batches reused existing
tests:

```text
Experimental batch A: 134 passed
Experimental batch B: 265 passed
Total: 399 passed
```

The batches covered runtime continuity, equity/prediction observers, Operator
extensions, Serenity, exchange adoption, shadow trades, attribution, Learning
contribution/lifecycle/filter control, skills, and persona learning/rollout.
They are executable contract evidence, not natural-runtime proof.

## First-principles check

The review preserves the current Alpha-first paper core:

- it does not make any Experimental capability a dependency of Golden Path;
- it does not require SHA-bound dogfood to establish static product facts;
- it does not add a detector, daemon, evidence database, or parallel test
  architecture;
- it splits composite claims when one sub-path works and another does not;
- it records only a concrete promise/consumer mismatch, rather than converting
  every absent E3 observation into an issue.

The result is therefore a narrower product truth map, not a new verification
product.
