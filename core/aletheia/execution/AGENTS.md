# core/aletheia/execution/ — Execution Gateway, coordinator, stops, reconciliation

Controlled path ([safety guide](../../../docs/operations/development/agent-guides/SAFETY_AND_TRUST.md)).
Approved behavior: [`execution_contract.md`](../../../docs/design/contracts/execution_contract.md).
Read the contract section before changing a state transition.

## Conventions

- The Gateway is the only path to an exchange, and only after Risk approval. Preserve causal
  IDs proposal → risk → order → fill → position; audits name the real actor and source.
- Vocabulary: Orders, Proposals and Positions carry `spot` / `usdt_futures`; instrument
  identity and `universe_registry` use `spot` / `perp`. Normalize explicitly at the boundary
  (e.g. `venue_capabilities._REGISTRY_MARKET_TYPE_ALIASES`); a raw comparison across the two
  silently matches nothing.
- Non-terminal Order states have one definition, `storage/order_states.py`. Every
  non-terminal state needs a bounded exit (contract §3.0b, §6.1).
- Never infer `filled` / `cancelled` from absence in an open-orders list; incomplete,
  unsupported or partial reads stay unresolved.
- State writers are owned. Adding a caller of a guarded writer (e.g. `transition_entry_plan`)
  means updating the allowlist in `tests/test_planning_writer_boundary.py` in the same change,
  with the contract section that authorizes it.
- Multi-row terminal transitions share one `repo.transaction(immediate=True)`.

## Tests

```sh
uv run pytest tests -q -m "domain_execution"
uv run pytest tests/specs/spec_execution.py tests/test_planning_writer_boundary.py -q
```
