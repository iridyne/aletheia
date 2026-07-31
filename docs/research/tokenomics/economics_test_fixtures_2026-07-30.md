# Economics Test Fixtures and Patterns — 2026-07-30

Frozen research evidence collected while scoping the `tokenomics`
branch. Purpose: catalogue the fixture and mock patterns already in
place for economics tests, so a Phase 1 `/api/economics/*` test suite
inherits from a known-working baseline instead of reinventing setup.

Sources: `tests/conftest.py`, `tests/test_economic_cli.py`,
`tests/test_economic_cost_tracing.py`, `tests/test_economic_usage.py`,
`tests/test_economic_viability.py`, `tests/test_status_routes.py`.

## Autouse fixtures every test inherits

`tests/conftest.py` installs several `autouse` fixtures that Phase 1
tests get for free:

- `_inmemory_db(request, tmp_path, monkeypatch)` — points `DATA_DIR`
  and `CONFIG_PATH` at `tmp_path`, resets the process-wide SQLite
  connection, opens a fresh `:memory:` connection, and runs
  `run_migrations()`. Skipped for tests marked `real_db`. Baseline
  schema is available before the test body starts.
- `_block_real_exchange_orders` — replaces `BitgetClient.place_order`
  with a raising stub so an unmocked test can never place a real
  order.
- `_fast_network_timeout`, `_fast_frontdoor_probe`, `_fast_port_probe`,
  `_no_network_market_data` — snip out slow or network-dependent code
  paths that would otherwise dominate wall time.
- `_shutdown_scheduler_after_test` — stops any APScheduler instance a
  test may have started.

Test tier and domain classifiers are also configured in `conftest.py`.
Phase 1 tests should self-classify with pytest markers:
`tier_integration` for route tests, `tier_unit` for reused-function
tests, `domain_runtime` for anything touching the broker or provider
layer.

## Economics-specific setup patterns

The four existing `test_economic_*.py` files converge on the same
setup shape:

1. **Install schema on demand.** `ensure_economics_schema()` is
   called at the top of each test (or its helper) — the migrations
   run by the autouse fixture do not install the economics tables,
   but the schema installer is idempotent and safe to call any
   number of times.
2. **Seed pricing via public writers.** `add_pricing_snapshot(...)`
   and `add_tool_cost_profile(...)` are used to install price
   contracts. This matches the production code path exactly and gives
   the test a real pricing_snapshot_id to compare against.
3. **Seed cost events via direct SQL INSERT.** Both `test_economic_cli`
   and `test_economic_viability` bypass the tracing decorator and
   INSERT rows directly into `runtime_cost_events`. Test helpers
   (`_insert_known_llm_cost`, per-file) parameterise `event_id`,
   `cost`, `run_id`, and `metadata`. This keeps the tests focused on
   the query behaviour, not the tracing wrapper.
4. **Fake a provider when needed.** `test_economic_cost_tracing`
   defines a `_MeasuredProvider(ModelProvider)` with a hard-coded
   `usage={"prompt_tokens": 1_000, "completion_tokens": 500}` and a
   `fail=True` toggle. Because it subclasses `ModelProvider`, the
   base-class `__init_subclass__` tracing decorator wraps its
   `complete`/`structured` automatically — so tests can exercise the
   real tracing path against a deterministic provider.
5. **Seed an AgentRun via the real repository.**
   `insert_agent_run(AgentRun(agent_name="...", trigger_type="test",
   permission_mode="Paper"))` gives you a real `run_id` that the
   ledger and the `AgentRun.cost_estimate` aggregator will pick up.
6. **Bind attribution ContextVars.**
   `set_current_agent_run_id(run.id)` and
   `bind_economic_attribution(logical_agent="...", evaluation_id="...")`
   are the same helpers pi_daemon / orchestrator use in production —
   tests exercise the identical binding path.
7. **Monkeypatch trading outcomes.** Viability tests
   monkeypatch
   `aletheia.analytics.outcome_integrity.eligible_strategy_outcomes`
   to return synthetic `[{"id": ..., "realized_pnl": ...}]` lists,
   isolating the economics logic from the position-lifecycle contract.
8. **Persist=False on viability builders.** `test_economic_viability`
   calls `build_system_viability(..., persist=False)` to avoid
   writing to `viability_reports` in the test DB. Phase 1's API tests
   should do the same for the GET endpoint.

## CLI test pattern

`tests/test_economic_cli.py` uses `typer.testing.CliRunner`:

```python
from typer.testing import CliRunner
from aletheia.main import app

runner = CliRunner()

def test_costs_command_renders_current_summary_field_names():
    ensure_economics_schema()
    # ... INSERT into runtime_cost_events ...
    result = runner.invoke(app, ["audit", "costs", "--days", "1"])
    assert result.exit_code == 0, result.output
    assert "Events: 1" in result.output
```

This is the pattern to duplicate if a new `aletheia economics …` CLI
namespace is ever introduced. For Phase 1 we probably keep the CLI as
is and only add HTTP.

## Route test pattern

`tests/test_status_routes.py` shows the idiomatic FastAPI route test:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from aletheia.server.app import app

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def client(tmp_path, monkeypatch):
    # ... same DATA_DIR / CONFIG_PATH monkeypatch as autouse ...
    # ... optional file-backed DB if the test needs real WAL semantics ...
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()

@pytest.mark.anyio
async def test_ready_is_lightweight_liveness(client):
    resp = await client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
```

For a `/api/economics/*` test the same shape applies:

- Reuse the `_inmemory_db` autouse (drop the file-backed DB variant
  — economics doesn't need WAL semantics).
- Call `ensure_economics_schema()` inside the fixture (or at the top
  of each test).
- Seed pricing + cost events with the helpers above.
- Send `await client.get("/api/economics/costs?days=1")`.
- Assert on `resp.status_code`, `resp.json()["event_count"]`,
  `resp.json()["known_cost_subtotal_usd"]`, etc.

The `httpx.AsyncClient` + `ASGITransport(app=app)` pair does NOT open
a network socket; requests are dispatched in-process. Suitable for
tests that run in the fast tier.

## What Phase 1 tests should assert

Sketch, not a spec, adapted from the CLI tests already in place:

- **Route shape.** `GET /api/economics/costs?days=1` returns 200 and
  a dict with keys `window_start`, `window_end`, `event_count`,
  `known_cost_subtotal_usd`, `unknown_event_count`, `by_type`,
  optionally `events` when `?include_events=true`. Contract mirrors
  `list_cost_summary`.
- **Kill switch honesty.** With `ALETHEIA_VIABILITY_AUDIT=0` (set
  via monkeypatch on `os.environ`), `GET /api/economics/viability`
  returns 503 with an explicit reason string.
- **Unknown-cost accounting.** Insert an LLM event with
  `estimated_cost_usd=NULL, cost_status='unknown'`; assert
  `resp.json()["unknown_event_count"] == 1` and
  `known_cost_subtotal_usd == 0`.
- **Attribution filter.** Insert two events with different `run_id`s;
  assert `?run_id=<one>` returns only the matching event.
- **Broker snapshot passthrough.** Start the broker with a fake
  submission, hit `GET /api/economics/broker`, assert
  `resp.json()["running"] is True` and the per-provider metric block
  shape matches `broker.py::_Metrics.snapshot`.
- **Model-health snapshot passthrough.** Force a model into
  cooldown via `fallback._record_model_failure(...)` calls; assert
  `GET /api/economics/model-health` reflects `cooling_down: true` and
  the correct `cooldown_remaining_sec`.

## Things NOT to test in Phase 1

- Whether `runtime_cost_events` correctly records real-provider
  attempts — that is already covered by `test_economic_cost_tracing`.
- Whether pricing computation is correct — already covered.
- Whether the viability audit's outcome-cohort filtering is correct
  — already covered.

The Phase 1 API layer only needs to prove: the routes exist, they
return the expected JSON shapes, they respect kill switches, and they
handle empty / unknown / windowed inputs honestly.

## Suggested pytest markers for Phase 1 tests

`@pytest.mark.tier_integration` for the route tests (they cross the
route + DB + tracing boundary), `@pytest.mark.domain_runtime` because
the surface is part of runtime/observability, and `@pytest.mark.fast`
so they stay in the default fast loop.
