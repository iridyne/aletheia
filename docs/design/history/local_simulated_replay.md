# Archived local simulator runtime replay

`runtime_replay_v1` represented the former `crypto-paper-mainline-v1` scope.
The fixed SQLite pack and JSON baseline remain under `tests/fixtures/` unchanged.
They preserve sanitized decision distributions, local orders/positions, forward
outcomes and legacy Learning exposure links. None of those records establishes
external exchange execution, economic qualification or current Learning authority.

The retired generator was `tests/fixtures/build_runtime_replay_v1.py` at commit
`8c62579ca`. It opened a source runtime read-only to check category presence,
then replaced every ID, symbol sequence, timestamp, price, quantity, return and
free-text value with deterministic fixture data. It created local-simulator
records against the then-current schema and could replace an existing output.
It has been removed from the executable tree; Git preserves its source.

The retained replay tests read the frozen pack without regenerating it, check
SQLite integrity and causal links, and compare the historical baseline. Current
outcome assessment yields zero genuine outcomes from the pack. The old scope
identity remains a historical fact, not a current supported-mode selector.
See [current runtime scope](../../product/ACTIVE_RUNTIME_SCOPE.md) for the active
boundary and [fixture documentation](../../../tests/fixtures/README.md) for use.
