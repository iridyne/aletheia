"""Spec: Cross-Market-Type Opportunity Deduplication.

The original 2026-07-02 planning note was not retained. This executable spec is
the durable regression evidence for the behavior.

When ``exchange.enabled_markets`` has both spot and usdt_futures enabled, the
legacy path in ``observed_instruments_from_config`` must NOT emit two instruments
per canonical symbol (would cause ``market_observer`` to run the LLM twice for
the same ETH, producing redundant 76%-vs-74% spot-vs-perp directional reads).

Design principle (first-principles, confirmed across Hummingbot + 12 academic
papers): directional analysis is instrument-agnostic. The same ETH reads ~76%
long whether the instrument is spot or perp. Separating by market_type is only
valid for funding-rate / basis arbitrage, which is an independent strategy
(FundingRateHarvester), not the directional observer pipeline.

Reuses ``data_layer_contract`` — no new trust boundary. Behavioral claims are
offlined (don't need LLM); e2e claims mock the LLM like test_entry_logic does.
"""

import inspect

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# OD-S01 — L2b Behavioral: legacy path emits ≤1 instrument per symbol
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("OD-S01", "data_layer_contract", "§3 legacy entry dedup", "L2b",
              "observed_instruments_from_config emits AT MOST one instrument per "
              "canonical symbol when both spot and usdt_futures are enabled and "
              "preferred_market_type is not 'spot'"),
)
def test_legacy_path_single_instrument_per_symbol():
    from aletheia.trading.instruments import observed_instruments_from_config
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.market_data.observed_symbols = ["BTC/USDT", "ETH/USDT"]
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "auto"

    insts = observed_instruments_from_config(cfg)
    by_symbol: dict[str, list[str]] = {}
    for i in insts:
        by_symbol.setdefault(i.symbol, []).append(i.market_type)

    assert len(insts) == 2, f"expected 2 instruments (one per symbol), got {len(insts)}"
    for sym, mts in by_symbol.items():
        assert len(mts) == 1, f"{sym} emitted {len(mts)} instruments: {mts}"
        assert mts[0] == "perp", (
            f"{sym} got market_type={mts[0]}; expected perp (auto bias)"
        )


@spec_claim(
    SpecClaim("OD-S01b", "data_layer_contract", "§3 preferred=spot override", "L2b",
              "with preferred_market_type='spot' and both markets enabled, "
              "observed_instruments_from_config emits spot, not perp"),
)
def test_legacy_path_respects_spot_preference():
    from aletheia.trading.instruments import observed_instruments_from_config
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "spot"

    insts = observed_instruments_from_config(cfg)
    assert len(insts) == 1
    assert insts[0].market_type == "spot"


# ---------------------------------------------------------------------------
# OD-S02 — L2a Structural: repo function accepts market kwarg
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("OD-S02", "data_layer_contract", "§3 DB dedup filter", "L2a",
              "find_open_opportunity_by_symbol_snapshot accepts an optional market "
              "kwarg; when provided, the query also filters on opportunities.market "
              "so a same-symbol opp from a different market (spot vs perp) does not "
              "collide. market=None preserves prior behavior."),
)
def test_repo_accepts_market_kwarg():
    from aletheia.storage.repositories import find_open_opportunity_by_symbol_snapshot

    sig = inspect.signature(find_open_opportunity_by_symbol_snapshot)
    params = sig.parameters
    assert "market" in params, "must accept market kwarg"
    # Default None = existing callers work unchanged.
    assert params["market"].default is None, "market must default to None"


@spec_claim(
    SpecClaim(
        "OD-S02e", "data_layer_contract", "§3 lifecycle identity dedup", "L2b",
        "Opportunity identity lookup for one universe snapshot includes reviewed rows, so a "
        "later observer pass cannot create a second opportunity or rewrite terminal history.",
        status="implemented",
    ),
)
def test_lifecycle_lookup_reuses_reviewed_opportunity(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Opportunity
    from aletheia.storage import repositories

    original = Opportunity(
        symbol="WLD/USDT",
        market="usdt_futures",
        direction_bias="long",
        confidence="medium",
        status="reviewed",
        universe_snapshot_id="snapshot-reviewed",
        universe_member_id="member-reviewed",
        scanner_family="momentum",
    )
    repositories.insert_opportunity(original)
    duplicate = Opportunity(
        symbol=original.symbol,
        market=original.market,
        direction_bias="long",
        confidence="medium",
        status="open",
        universe_snapshot_id=original.universe_snapshot_id,
        universe_member_id=original.universe_member_id,
        scanner_family=original.scanner_family,
    )
    repositories.insert_opportunity(duplicate)

    found = repositories.find_opportunity_by_symbol_snapshot(
        original.symbol,
        original.universe_snapshot_id,
        market=original.market,
    )

    assert found is not None
    assert found["id"] == original.id
    assert found["status"] == "reviewed"


@spec_claim(
    SpecClaim("OD-S02b", "data_layer_contract", "§3 signal_emitter passes market", "L2a",
              "signal_emitter passes market= to the lifecycle identity lookup "
              "so same-symbol opps in different markets don't collide (OD-S02 lock)"),
)
def test_signal_emitter_passes_market_filter():
    from aletheia.universe import signal_emitter
    src = inspect.getsource(signal_emitter.emit_deterministic_opportunities)
    assert "market=" in src and "find_opportunity_by_symbol_snapshot" in src, (
        "signal_emitter must pass market= to the lifecycle identity lookup"
    )


@spec_claim(
    SpecClaim("OD-S02d", "data_layer_contract", "§3 universe preferred market", "L2b",
              "UniverseService's same-symbol member dedup keeps the configured "
              "preferred market instrument (auto→perp, spot→spot) instead of "
              "letting the highest-scoring spot row crowd out perp rows"),
)
def test_universe_member_dedup_respects_preferred_market():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.service import _dedupe_by_symbol_preferred_market

    allocated = [
        {"symbol": "BTC/USDT", "market_type": "spot", "total_score": 90.0},
        {"symbol": "BTC/USDT", "market_type": "perp", "total_score": 50.0},
        {"symbol": "ETH/USDT", "market_type": "spot", "total_score": 60.0},
    ]

    cfg = AletheiaConfig()
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "auto"

    deduped = _dedupe_by_symbol_preferred_market([dict(x) for x in allocated], cfg)
    by_symbol = {x["symbol"]: x for x in deduped}
    assert by_symbol["BTC/USDT"]["market_type"] == "perp", (
        "auto preference should keep perp even when the spot duplicate has a higher score"
    )

    cfg.exchange.preferred_market_type = "spot"
    deduped = _dedupe_by_symbol_preferred_market([dict(x) for x in allocated], cfg)
    by_symbol = {x["symbol"]: x for x in deduped}
    assert by_symbol["BTC/USDT"]["market_type"] == "spot"


@spec_claim(
    SpecClaim("OD-S02c", "data_layer_contract", "§3 market_observer passes market", "L2a",
              "market_observer passes market= to the lifecycle identity lookup "
              "so same-symbol opps in different markets don't overwrite each other"),
)
def test_market_observer_passes_market_filter():
    from aletheia.agents import market_observer
    src = inspect.getsource(market_observer._analyze_symbol)
    assert "market=" in src and "find_opportunity_by_symbol_snapshot" in src, (
        "market_observer._analyze_symbol must pass market= to "
        "find_opportunity_by_symbol_snapshot"
    )
    terminal_guard = 'existing.get("status") != "open"'
    assert terminal_guard in src
    assert src.index(terminal_guard) < src.index("update_open_opportunity"), (
        "a reviewed opportunity must return before the open-only annotation CAS"
    )


# ---------------------------------------------------------------------------
# OD-S03 — L2b Behavioral: e2e single opp per symbol per cycle
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("OD-S03", "data_layer_contract", "§3 e2e dedup", "L2b",
              "without deterministic owners, Market Observer creates no standalone "
              "spot or perp Opportunity"),
)
async def test_e2e_observer_dedups(tmp_path, monkeypatch):
    """E2E: two symbols × both markets enabled → exactly 2 opportunities (one per
    symbol), not 4 (2 per symbol). Mock the LLM like test_entry_logic does."""
    import sqlite3
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage import repositories as repo
    from aletheia.agent_runtime.orchestrator import run_agent

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT", "ETH/USDT"]
    cfg.market_data.observed_xstocks = []
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "auto"
    save_config(cfg)

    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    repo.insert_market_snapshot(MarketSnapshot(symbol="ETH/USDT", market_type="spot", price=3000))

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
    await run_agent("market_observer", trigger_type="manual")

    opps = repo.list_opportunities()
    by_symbol: dict[str, int] = {}
    for o in opps:
        by_symbol[o["symbol"]] = by_symbol.get(o["symbol"], 0) + 1

    assert by_symbol == {}


@spec_claim(
    SpecClaim(
        "OD-S04",
        "data_layer_contract",
        "§3 observer rejection evidence durability",
        "L2b",
        "When Universe admission is unavailable, Observer fails closed before context, "
        "LLM, evidence, or durable round-robin state.",
        status="implemented",
    ),
)
async def test_observer_rejection_evidence_failure_is_retryable(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.orchestrator import run_agent
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.universe.enabled = False
    save_config(cfg)

    async def quiet_market_context(*args, **kwargs):
        return "BTC/USDT quiet market", {"fallback": False, "errors": []}

    monkeypatch.setattr(
        "aletheia.trading.market_context.build_market_context",
        quiet_market_context,
    )
    monkeypatch.setattr(
        "aletheia.agents.market_observer._has_sufficient_signal",
        lambda context: False,
    )
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.repo.insert_filter_decision",
        lambda decision: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    result = await run_agent("market_observer", trigger_type="manual")

    assert result["outputs"] == []
    assert result["errors"] == []
    assert get_db().execute(
        "SELECT 1 FROM filter_decisions WHERE filter_name='observer_pre_filter'"
    ).fetchone() is None
    assert get_db().execute(
        "SELECT 1 FROM settings WHERE key='agent.market_observer.round_robin.v1'"
    ).fetchone() is None


@spec_claim(
    SpecClaim(
        "OD-S05",
        "data_layer_contract",
        "§3 observer opportunity emission atomicity",
        "L2b",
        "Market Observer has no Opportunity producer path and cannot write a "
        "market_observer opportunity_emission pass fact.",
        status="implemented",
    ),
)
async def test_observer_emitted_evidence_failure_rolls_back_opportunity(
    tmp_path, monkeypatch
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.orchestrator import run_agent
    from aletheia.analytics import filter_evidence
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.sqlite import get_db

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.exchange.preferred_market_type = "spot"
    cfg.universe.enabled = False
    save_config(cfg)

    async def deterministic_market_context(*_args, **_kwargs):
        return "BTC/USDT breakout with RSI 68 and positive momentum", {
            "fallback": False,
            "errors": [],
        }

    async def deterministic_read(*_args, **_kwargs):
        return {
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 90,
            "rationale": "breakout confirmed",
            "key_observations": ["RSI 68"],
        }, None

    real_insert = filter_evidence.repo.insert_filter_decision

    def fail_only_observer_emitted_fact(fact):
        if (
            fact.filter_name == "opportunity_emission"
            and fact.stage == "market_observer"
            and fact.reason_code == "opportunity_emitted"
        ):
            raise RuntimeError("observer emitted evidence unavailable")
        return real_insert(fact)

    monkeypatch.setattr(
        "aletheia.trading.market_context.build_market_context",
        deterministic_market_context,
    )
    monkeypatch.setattr(
        "aletheia.agents.market_observer.llm_structured",
        deterministic_read,
    )
    monkeypatch.setattr(
        "aletheia.agents.market_observer._has_sufficient_signal",
        lambda _context: True,
    )
    monkeypatch.setattr(
        filter_evidence.repo,
        "insert_filter_decision",
        fail_only_observer_emitted_fact,
    )

    result = await run_agent("market_observer", trigger_type="manual")

    assert result["outputs"] == []
    assert result["errors"] == []
    db = get_db()
    assert db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM filter_decisions "
        "WHERE filter_name='observer_pre_filter'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM filter_decisions "
        "WHERE filter_name='opportunity_emission' AND stage='market_observer'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT 1 FROM settings WHERE key='agent.market_observer.round_robin.v1'"
    ).fetchone() is None
