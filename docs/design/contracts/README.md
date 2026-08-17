# Current Behavioral Contracts

Files in this directory describe behavior that is currently approved and
observable. They are not roadmaps.

Every Contract should identify:

- the production entry point;
- durable state and final consumer;
- observable end result;
- invariants and trust boundaries;
- failure and recovery semantics;
- executable evidence for current behavior.

Future targets, unwired components, and desired guarantees belong in
[`../proposals/`](../proposals/). Known mismatches belong in GitHub Issues. A
Contract may link to either, but must not express them as current behavior.

Legacy or retired mechanisms belong in [`../history/`](../history/).

Use [template.md](template.md) for new Contracts. Contract changes and their
executable claims must remain aligned, but claim conformance does not determine
Product Support.

Current framework-wide contracts include the
[LLM Inference Harness Contract](llm_inference_harness_contract.md), which owns
logical model-call identity, prompt/context/tool/output policy, and its link to
physical tokenomics attempts.

Market-data computation and storage lifecycle are owned by the
[Technical Analysis and OHLCV Tiering Contract](technical_analysis_contract.md).
Exact-timeframe and real-field provider results are owned by the
[OHLCV Provider Result Contract](ohlcv_provider_result_contract.md).
Collector execution facts and persisted dataset freshness are kept separate by
the [Market Data Coverage and Collector Health Contract](market_data_coverage_contract.md).

Profit-loop replay, candidate/Position denominator separation, frozen opening
hypotheses, exploratory counterfactuals, and seven-layer winner gates are owned
by the [Profit-loop observability contract](profit_loop_observability_contract.md).

Learning Knowledge empty, stale, and unavailable presentation semantics are
owned by the
[Dashboard Data Presentation Contract](dashboard_data_presentation_contract.md).

The [Universe Twin Shadow Evidence Contract](universe_twin_shadow_contract.md)
owns the current non-authoritative continuous-score evidence, allocation replay,
identity, persistence, and retention boundary.

The [Trading Mandate Contract](trading_mandate_contract.md) owns the single
optional observe-only mandate's canonical identity, deterministic fit evidence,
Proposal freeze, Position Replay exposure, and descriptive compliance reports.

The [Settings Control Persistence Contract](settings_control_contract.md) owns
the explicit immediate-versus-staged frontend control semantics and the checked-in
Settings switch inventory.
