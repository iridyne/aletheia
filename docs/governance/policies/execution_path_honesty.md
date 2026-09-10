# Execution Path Honesty

The [June 2026 execution-path analysis](../../design/history/execution_path_honesty_202606.md)
is archived. Its Paper Trader, local-simulator, RuleSet and strategy-promotion
observations describe that historical implementation and are not current
requirements or permission to restore those paths.

Current entry behavior follows Candidate → Planning Generation → Planner →
Compiler → Entry Plan. The [Execution contract](../../design/contracts/execution_contract.md)
defines the Gateway boundary; the [Risk contract](../../design/contracts/risk_engine_contract.md)
defines account and order approval. Neither a model response, a stored strategy
label nor a passing test proves that an order path exists or that a trade is
profitable. Current product and runtime evidence are governed by the
[Admission Gates](../../product/gates/PHASE_GATES.md).

When reviewing a path, trace its producer, consumer, persisted identity and
actual runtime authority. Keep model inference, deterministic checks, order
submission and private-fill confirmation distinct in claims and evidence.
