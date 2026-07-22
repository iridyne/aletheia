"""Shared test fixtures + safety guards.

`bitget_demo` (now the default `paper_execution_mode`) places REAL orders on Bitget's
demo API. Secrets are read from the OS keyring first, which the per-test `tmp_path`
DATA_DIR monkeypatch does NOT sandbox — so without a guard an unmocked test that runs
paper_trader to a trade could fire a real demo order over the network during pytest.

This autouse guard makes "tests never touch the real exchange" structural: it replaces
`BitgetClient.place_order` with a loud error by default. Tests that exercise the
exchange path provide their own `monkeypatch.setattr(...place_order, fake)`, which
overrides this within the test body. A test that reaches the exchange without a mock
fails loudly instead of silently placing a real order.
"""

import sqlite3
import pytest

# ---------------------------------------------------------------------------
# Windows event-loop policy (D17-1)
# ---------------------------------------------------------------------------
# The test process otherwise uses the default ProactorEventLoop, on which broker
# tests calling asyncio.run() (okx/binance/bitget) can hang ~255s on IOCP teardown,
# and the smoke subprocess in test_update intermittently fails with "cannot schedule
# new futures after shutdown". Apply the same process-level SelectorEventLoop policy
# the production entry-points use (5bfa4a0) so the test process matches production.
# Idempotent; no-op off Windows. MUST run before any asyncio use → at import time.
from aletheia.async_utils import apply_windows_selector_policy as _apply_selector_policy
_apply_selector_policy()

# ---------------------------------------------------------------------------
# Hypothesis global settings
# ---------------------------------------------------------------------------
try:
    from hypothesis import settings as _h_settings, HealthCheck
    _h_settings.register_profile("ci", max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    _h_settings.register_profile("dev", max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    _h_settings.load_profile("dev")
except ImportError:
    pass  # hypothesis is optional; property tests are skipped if missing


_PYTEST_MARKERS = [
    "fast: default AI feedback set; cheap local tests suitable for every bug-fix loop.",
    "integration: tests that cross route, DB, scheduler, broker, runtime, or service boundaries.",
    "e2e: end-to-end pipeline tests (deselect with -m 'not e2e').",
    "real_place_order: test exercises BitgetClient.place_order directly with httpx "
    "mocked (no network) - opt out of the place_order guard.",
    "real_llm: test provides its own LLM mock - opt out of the fast-fail guard.",
    "real_network: test needs real network calls - opt out of the market-data guard.",
    "real_db: test requires a real file-backed SQLite DB (WAL/lock/cross-connection) "
    "- opt out of the in-memory DB injection.",
    "spec: spec-driven development test",
    "L2a: structural spec check (static, fast)",
    "L2b: behavioral spec check (needs fixtures)",
    "L2c: invariant spec check (cross-operation)",
    "online: test hits a live external API (network-gated; skipped when offline).",
    "slow: manually marked slow tests; daily loop can skip with -m 'not slow'.",
    "slow_optimizable: duration classifier tag for slow call-path tests worth inspecting.",
    "slow_structural: duration classifier tag for slow setup/teardown/framework overhead.",
    "tier_unit: fast unit/pure-function tests.",
    "tier_integration: tests that cross module, route, DB, scheduler, broker, or runtime boundaries.",
    "tier_e2e: end-to-end pipeline tests.",
    "tier_spec: executable specification tests under tests/specs.",
    "domain_operator: System Operator / agent shell / operator loop / trading decision / proposal tests.",
    "domain_universe: universe scanner / allocator / signal / filter evidence / filter replay tests.",
    "domain_serenity: Serenity Lens / research pipeline tests.",
    "domain_risk: Risk Engine / kill switch / risk route tests.",
    "domain_execution: order, broker, position, trade, outcome, exchange sync, PnL, reconciliation, sizing, and execution-path tests.",
    "domain_market_data: market data, exchange data, price feed, collector, OHLCV, orderbook, instrument, and data freshness tests.",
    "domain_learning: knowledge, learning, dreaming, lesson, feedback, alpha attribution, analytics, and experience loop tests.",
    "domain_frontend: frontend/static UI source checks and UI component tests.",
    "domain_runtime: gateway, scheduler, runtime continuity, lifecycle, supervisor, workflow, connectivity, and notification tests.",
    "domain_storage: storage, DB, migration, retention, backup, IO honesty, and audit-log tests.",
    "domain_security: secrets, auth, permission, trust boundary, export redaction, and safety guard tests.",
    "domain_config: config/settings tests.",
    "domain_misc: fallback domain marker when no narrower domain applies.",
]

_INTEGRATION_TOKENS = (
    "acceptance",
    "broker",
    "daemon",
    "frontdoor",
    "gateway",
    "integration",
    "lifecycle",
    "routes",
    "runtime",
    "scheduler",
    "server",
    "service",
    "shell",
    "status",
    "storage",
    "supervisor",
    "workflow",
    "workbench",
)

_SLOW_PATH_TOKENS = (
    # Current --durations=20 hot spots and intentionally broad workflow tests.
    "test_agent_runtime.py",
    "test_agent_shell.py",
    "test_doctor.py",
    "test_doctor_content_probes.py",
    "test_doctor_json.py",
    "test_operator_routes.py",
    "test_operator_shell_supervision.py",
    "test_serenity_workbench.py",
    "spec_serenity_verdict.py",
    "spec_learning_pipeline.py",
    "spec_pipeline_entry_enrichment.py",
    # Training / sweep / remote-service paths should not be in the default loop.
    "optuna",
    "training",
    "remote",
    "live_e2e",
)

_DOMAIN_PATTERNS = [
    ("domain_operator", (
        "operator", "agent_shell", "pi_daemon", "command",
        "entry_logic", "decision", "proposal",
    )),
    ("domain_universe", (
        "universe",
        "candidate_supply", "filter_evidence", "filter_replay", "filter_shadow",
    )),
    ("domain_serenity", ("serenity",)),
    ("domain_risk", ("risk", "kill_switch", "guard", "daily_loss")),
    ("domain_execution", (
        "trade", "trading", "position", "broker", "bitget", "okx", "order", "fill",
        "tpsl", "leverage", "execution", "paper_",
        "outcome", "exchange", "pnl", "disappearance", "reconcil",
        "sizing", "uta", "exit_policy", "account_loss",
    )),
    ("domain_market_data", (
        "market", "price", "collector", "ccxt", "coingecko", "macro", "regime",
        "xstocks", "prediction",
        "divergence", "event_trigger", "ohlcv", "instrument_id", "enriched", "orderbook",
    )),
    ("domain_learning", (
        "knowledge", "learning", "dreaming", "lesson", "feedback",
        "hypothesis", "anti_hallucination",
        "alpha", "analytics", "experience", "self_evolution",
    )),
    ("domain_frontend", (
        "frontend", "webui", "navigation_ia", "settings_ui", "build_config",
        "overview", "_ui",
    )),
    ("domain_runtime", (
        "runtime", "scheduler", "lifecycle", "supervisor", "gateway", "frontdoor",
        "startup", "resume", "health", "doctor", "pidfile", "update", "autostart",
        "workflow", "connectivity", "event_loop", "self_heal", "layered_pause",
        "lifespan", "git_hook", "openapi", "net_fallback", "notification", "telegram",
    )),
    ("domain_storage", (
        "storage", "db", "retention", "audit", "fts", "history",
        "io_honesty", "backup",
    )),
    ("domain_security", (
        "security", "secret", "auth", "permission", "codex",
        "trust_boundary", "export_redact",
    )),
    ("domain_config", ("config", "settings")),
]


def pytest_configure(config):
    for marker in _PYTEST_MARKERS:
        config.addinivalue_line("markers", marker)


def _normalized_node_path(item) -> str:
    path = getattr(item, "path", None) or getattr(item, "fspath", "")
    normalized = str(path).replace("\\", "/").lower()
    tests_idx = normalized.rfind("tests/")
    return normalized[tests_idx:] if tests_idx >= 0 else normalized


def _classify_test_tier(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "tests/e2e/" in path or name.endswith("_e2e.py") or "_e2e" in name:
        return "tier_e2e"
    if "tests/specs/" in path or name.startswith("spec_") or name == "test_spec_conformance.py":
        return "tier_spec"
    if any(token in name for token in _INTEGRATION_TOKENS):
        return "tier_integration"
    return "tier_unit"


def _is_slow_test(path: str, item=None) -> bool:
    if "tests/e2e/" in path:
        return True
    if any(token in path for token in _SLOW_PATH_TOKENS):
        return True
    if item is not None:
        if item.get_closest_marker("online") or item.get_closest_marker("real_network"):
            return True
    return False


def _classify_test_domains(path: str) -> list[str]:
    domains = [
        marker
        for marker, tokens in _DOMAIN_PATTERNS
        if any(token in path for token in tokens)
    ]
    return domains or ["domain_misc"]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    for item in items:
        path = _normalized_node_path(item)
        tier = _classify_test_tier(path)
        item.add_marker(getattr(pytest.mark, tier))
        if tier == "tier_unit" and not _is_slow_test(path, item):
            item.add_marker(pytest.mark.fast)
        elif tier == "tier_integration":
            item.add_marker(pytest.mark.integration)
        if tier == "tier_e2e":
            item.add_marker(pytest.mark.e2e)
        if _is_slow_test(path, item):
            item.add_marker(pytest.mark.slow)
        for domain in _classify_test_domains(path):
            item.add_marker(getattr(pytest.mark, domain))


@pytest.fixture(autouse=True)
def _shutdown_scheduler_after_test():
    """Stop any BackgroundScheduler a test started, so its thread pool doesn't try to
    submit jobs after interpreter shutdown — the apscheduler atexit race ('cannot
    schedule new futures after shutdown') that intermittently failed test_update in
    the full suite. stop_scheduler() is None-safe (no-op if none was started)."""
    yield
    try:
        from aletheia.agent_runtime.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


@pytest.fixture
def api_only_runtime(monkeypatch):
    """Run the real ASGI app without schedulers or external startup probes."""
    monkeypatch.setenv("ALETHEIA_RUNTIME_MODE", "test")
    monkeypatch.setenv("ALETHEIA_RUNTIME_ID", "runtime-test-api-only")


@pytest.fixture(autouse=True)
def _block_real_exchange_orders(request, monkeypatch):
    # Tests that verify place_order's own body-building mock httpx instead, so they
    # opt out of this guard via @pytest.mark.real_place_order.
    if request.node.get_closest_marker("real_place_order"):
        return
    from aletheia.exchanges.bitget import client as bg

    async def _blocked(self, order):  # noqa: ARG001
        raise RuntimeError(
            "BitgetClient.place_order reached the real exchange in a test. "
            "Mock it with monkeypatch.setattr(BitgetClient, 'place_order', fake) "
            "or route the proposal through paper_internal."
        )

    monkeypatch.setattr(bg.BitgetClient, "place_order", _blocked)


@pytest.fixture(autouse=True)
def _fast_network_timeout(request, monkeypatch):
    """Set very short httpx connect timeout so external calls fail fast.

    Does NOT block ASGI TestClient (which uses transport, not real TCP).
    LLM/exchange calls that go to real hosts fail in <50ms instead of 60s.
    Also disables model-discovery network call inside fallback logic.
    """
    if request.node.get_closest_marker("real_network"):
        return
    from aletheia import net as _net
    import httpx as _httpx

    _original_httpx_client = _net.httpx_client

    def _fast_httpx_client(timeout: float = 30, **kwargs):
        fast_timeout = _httpx.Timeout(0.05, connect=0.05)
        return _original_httpx_client(timeout=fast_timeout, **kwargs)

    monkeypatch.setattr(_net, "httpx_client", _fast_httpx_client)

    # Also patch httpx.AsyncClient directly so modules that do
    # `from aletheia.net import httpx_client` (binding the function at import time)
    # still get the short connect timeout.  ASGI transports are left untouched
    # (transport= kwarg present → no real TCP, timeout is irrelevant there).
    _OrigAsyncClient = _httpx.AsyncClient

    class _FastAsyncClient(_OrigAsyncClient):
        def __init__(self, *args, **kwargs):
            if "transport" not in kwargs:
                kwargs["timeout"] = _httpx.Timeout(0.05, connect=0.05)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(_httpx, "AsyncClient", _FastAsyncClient)

    # Disable list_models() network discovery in fallback to avoid extra round-trips.
    # Tests that provide their own LLM mock (real_llm marker) keep full fallback logic.
    if not request.node.get_closest_marker("real_llm"):
        import aletheia.model_provider.fallback as _fb

        async def _fast_candidates(provider_name, provider):
            primary = str(getattr(provider, "model", "") or "")
            return [primary] if primary else []

        monkeypatch.setattr(_fb, "_candidate_models", _fast_candidates)

    # Eliminate retry delays in startup/resume LLM gate (avoids 15s×2 sleeps).
    # Only skips long sleeps (>2s); tests with explicit short delays (1s) still work.
    import aletheia.agent_runtime.startup_gate as _sg

    _real_asyncio_sleep = _sg.asyncio.sleep

    async def _skip_long_sleep(seconds):
        if seconds > 2:
            return
        await _real_asyncio_sleep(seconds)

    monkeypatch.setattr(_sg.asyncio, "sleep", _skip_long_sleep)


@pytest.fixture(autouse=True)
def _fast_frontdoor_probe(monkeypatch):
    """Shorten frontdoor timeouts from their production values to 50ms.

    Two clients bypass the _fast_network_timeout guard:
    - _probe_backend() creates its own AsyncClient with hardcoded 0.8s timeout.
    - _get_proxy_client() creates a persistent proxy client with connect=5.0s.
    Both are monkeypatched here.  The proxy client singleton is also reset so the
    patched timeout takes effect even if a prior test already built the client.
    """
    import aletheia.gateway.frontdoor as _fd
    monkeypatch.setattr(_fd, "_PROBE_TIMEOUT", 0.05)
    monkeypatch.setattr(_fd, "_PROXY_CONNECT_TIMEOUT", 0.05)
    # Force the proxy singleton to be rebuilt with the patched timeout.
    original = _fd._proxy_client
    _fd._proxy_client = None
    yield
    _fd._proxy_client = original


@pytest.fixture(autouse=True)
def _fast_port_probe(monkeypatch):
    """Shorten TCP port-liveness timeout from 0.5s to ~0ms.

    On Windows, connect() to a port with no listener waits the full settimeout()
    instead of returning ECONNREFUSED immediately.  Tests that call port_in_use()
    (directly or via wait_port_free) on ports that are genuinely free would
    otherwise block 0.5s per probe.
    """
    import aletheia.gateway.pidfile as _pf
    monkeypatch.setattr(_pf, "_PORT_PROBE_TIMEOUT", 0.001)


@pytest.fixture(autouse=True)
def _no_network_market_data(request, monkeypatch):
    """Prevent CCXT/exchange network calls from blocking tests.

    Returns empty data instantly instead of waiting for TCP timeouts to
    remote exchanges. Tests that need real market data responses should
    mock the provider themselves at a higher level.
    """
    if request.node.get_closest_marker("real_network"):
        return
    from aletheia.data.ccxt_provider import CCXTProvider

    async def _empty_ohlcv(self, symbol, *, timeframe="1h", limit=100):
        return []

    async def _empty_ticker(self, symbol):
        from aletheia.data.providers import Ticker
        return Ticker(symbol=str(symbol), last_price=50000.0,
                      volume_24h=1_000_000.0, change_pct_24h=1.5)

    monkeypatch.setattr(CCXTProvider, "get_ohlcv", _empty_ohlcv)
    monkeypatch.setattr(CCXTProvider, "get_ticker", _empty_ticker)
    # close() is NOT patched: the fake data methods above create no real CCXT
    # session, so the real idempotent close() is safe. Patching it to a no-op
    # (#238) prevented cleanup of any owned session opened by unguarded paths.


# ---------------------------------------------------------------------------
# In-memory SQLite injection
#
# Tests that call `sq._db_conn = conn` directly (setup_env helpers) override this
# fixture's connection — that's intentional. Tests that need a REAL file-backed DB
# for cross-connection WAL/lock tests (test_storage._real_db_env,
# test_runtime.setup_real_db_env) call `sq._db_conn = None` themselves, which clears
# the fixture's connection and lets get_db() open a real file connection instead.
#
# The autouse fixture therefore only benefits tests that DON'T have their own
# setup_env — unit tests, route tests, etc. — giving them a pre-migrated :memory: DB
# with zero disk I/O.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _inmemory_db(request, tmp_path, monkeypatch):
    """Inject a per-test in-memory SQLite DB for tests that don't set up their own.

    Skipped for tests marked `real_db` (WAL/lock/cross-connection tests).
    Tests with their own setup_env that assign sq._db_conn override this connection.
    """
    if request.node.get_closest_marker("real_db"):
        yield
        return

    import aletheia.config.loader as _loader
    import aletheia.storage.sqlite as _sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    # Point DATA_DIR at tmp_path so file artifacts (logs, markdown) still land on disk.
    monkeypatch.setattr(_loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_loader, "CONFIG_PATH", tmp_path / "config.yaml")

    reset_db()
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _sq._db_conn = conn
    run_migrations()

    yield

    reset_db()
