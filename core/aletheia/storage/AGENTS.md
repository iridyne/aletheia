# core/aletheia/storage/ — SQLite, migrations, repositories

Read [STATE_AND_DATA](../../../docs/operations/development/agent-guides/STATE_AND_DATA.md) and
[`data_model.md`](../../../docs/design/data/data_model.md) before changing persistence.

## Conventions

- Migrations: stable ID plus immutable checksum ledger, additive only (new tables, new
  columns with defaults). An applied migration's ID and definition are frozen: a deployed
  database that applied it fails closed against code that no longer knows it.
- Writes go through the process-wide `_WRITE_LOCK` in `sqlite.py` (WAL, 30 s `busy_timeout`,
  one thread-local connection). No transaction spans `await` or network/file I/O.
- Shared helpers already exist; import them instead of writing a private copy:
  `time_utils` (`utcnow`, `utcnow_iso`), `table_utils.table_exists`, and
  `aletheia.utils.json_utils` (`canonical_json` / `_safe` / `_strict`). Pick the variant whose
  flags match: changing flags changes hashed bytes.
- `order_states.py` is the one definition of non-terminal Order states.
- Retention never prunes trust-bearing audit-chain or learning tables
  (`tests/test_architecture_fitness.py` pins both lists).

## Tests

```sh
uv run pytest tests -q -m "domain_storage"
uv run pytest tests/specs/spec_data_model.py -q
```
