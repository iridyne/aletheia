# core/aletheia/risk/ — Risk Engine and kill switch

Controlled path ([safety guide](../../../docs/operations/development/agent-guides/SAFETY_AND_TRUST.md)).
Approved behavior: [`risk_engine_contract.md`](../../../docs/design/contracts/risk_engine_contract.md).

## Conventions

- `evaluate_proposal` is the single entry point, serialized by `_eval_lock`; Risk approval
  precedes every execution, and the Gateway re-checks live/account mode.
- Never weaken or remove a risk check, or change a hard limit or threshold, without explicit
  owner approval (contract §6, trust §6.3). A new check persists its `RiskDecision`, is
  visible in API/UI, and is tested on both approved and rejected paths.
- Native Proposals accept only `_NATIVE_MARKET_TYPES` (`spot`, `usdt_futures`). Features keyed
  by instrument identity (`universe_registry`: `perp`) must be read through
  `_physical_market_type`, not the raw Proposal value; otherwise the check skips silently.
- "Insufficient data → skip" policies are contract text; changing one to fail-closed (or the
  reverse) is a D1 policy change, not a fix.

## Tests

```sh
uv run pytest tests -q -m "domain_risk"
uv run pytest tests/specs/spec_risk_engine.py tests/specs/spec_pipeline_risk_gate.py -q
```
