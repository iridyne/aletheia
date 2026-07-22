"""Spec: Universe Pipeline integrity (scanner → allocator → signal_emitter → outcomes)

Executable claims for the Scanner/Observer audit fixes (2026-07-01).
Source: docs/plans/scanner_observer_audit_fix_plan_2026-07-01.md
        docs/research/UNIVERSE_SCANNER_AUDIT_2026-07-01.md

Locks the highest-leverage fix (forward-outcome direction must come from the same
source as the emitted opportunity) and the config-consumption / market_type /
feature-keying invariants against regression.
"""

import asyncio
from contextlib import nullcontext
import inspect

from tests.specs.spec_meta import SpecClaim, spec_claim


def _assert_emission_failure_cannot_publish_partial_universe_refresh(
    tmp_path, monkeypatch, failure_kind, *, caller_owns_transaction=False
):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseMember, UniverseSnapshot
    from aletheia.storage.sqlite import get_db
    from aletheia.universe import service
    from aletheia.universe.signal_emitter import EmissionEvidenceError

    cfg = load_config()
    cfg.universe.enabled = True
    cfg.regime.enabled = False
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.exchange.preferred_market_type = "spot"
    cfg.market_data.ccxt_exchange = "bitget"

    feature = {
        "asset_id": "asset-emission-retry",
        "symbol": "BTC/USDT",
        "venue": "bitget",
        "market_type": "spot",
        "last_price": 60_000.0,
        "quote_volume_24h": 1_000_000_000.0,
        "spread_bps": 2.0,
        "execution_eligible": True,
    }
    scanner_result = {
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "opportunity_score": 70.0,
        "all_scores": {"momentum": 70.0},
        "reasons": ["positive momentum"],
        "risk_flags": [],
    }
    allocated = [{
        **feature,
        **scanner_result,
        "total_score": 70.0,
        "tradability_score": 90.0,
        "liquidity_score": 90.0,
        "ttl_sec": 3600,
    }]
    previous = UniverseSnapshot(
        id="previous-healthy-universe",
        market_type="crypto",
        status="active",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_universe_snapshot(previous)
    repo.insert_universe_member(UniverseMember(
        id="previous-healthy-member",
        snapshot_id=previous.id,
        asset_id="previous-asset",
        symbol="ETH/USDT",
        market_type="spot",
        rank=1,
        total_score=80.0,
        scanner_family="momentum",
        scanner_direction="long",
        expires_at="2099-01-01T00:00:00+00:00",
    ))

    async def sync_registry(_cfg):
        return 1

    async def collect_features(_assets, _cfg, *, scanner_run_id):
        assert scanner_run_id
        return [feature]

    async def settle_due():
        return 0

    failure_code = (
        "opportunity_emission_evidence_failed:snapshot:member"
        if failure_kind == "evidence"
        else "opportunity_emission_runtime_failed:snapshot"
    )

    def emission_failure(*_args, **_kwargs):
        error_type = EmissionEvidenceError if failure_kind == "evidence" else RuntimeError
        raise error_type(failure_code)

    monkeypatch.setattr(service, "load_config", lambda: cfg)
    monkeypatch.setattr(service.repo, "mark_outlier_outcomes_failed", lambda: 0)
    monkeypatch.setattr(
        service.repo,
        "list_universe_assets",
        lambda **_kwargs: [feature],
    )
    monkeypatch.setattr(
        "aletheia.universe.registry.sync_registry", sync_registry
    )
    monkeypatch.setattr(
        "aletheia.universe.features.collect_features", collect_features
    )
    monkeypatch.setattr(
        "aletheia.universe.filters.apply_tradability_filters",
        lambda rows, *_args, **_kwargs: rows,
    )
    monkeypatch.setattr(
        "aletheia.universe.scanners.run_all_scanners",
        lambda _row: scanner_result,
    )
    monkeypatch.setattr(
        "aletheia.universe.allocator.compute_total_score",
        lambda *_args, **_kwargs: 70.0,
    )
    monkeypatch.setattr(
        "aletheia.universe.allocator.get_family_multipliers", lambda: {}
    )
    monkeypatch.setattr(
        "aletheia.universe.allocator.allocate_universe",
        lambda *_args, **_kwargs: allocated,
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.settle_due", settle_due
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.register_pending",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "aletheia.analytics.causal_controls.record_universe_control_cohort",
        lambda **_kwargs: None,
    )
    experiment_outcome_registrations = []
    monkeypatch.setattr(
        service,
        "_register_experiment_candidate_outcomes",
        lambda *_args, **_kwargs: experiment_outcome_registrations.append(True),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.get_latest_volume_ranking", lambda: None
    )
    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.emit_deterministic_opportunities",
        emission_failure,
    )

    caller_transaction = repo.transaction() if caller_owns_transaction else nullcontext()
    with caller_transaction:
        result = asyncio.run(service.UniverseService().refresh())

    assert result is None
    db = get_db()
    snapshots = db.execute(
        "SELECT id,status FROM universe_snapshots ORDER BY rowid"
    ).fetchall()
    assert [dict(row) for row in snapshots] == [{
        "id": previous.id,
        "status": "active",
    }]
    members = db.execute(
        "SELECT id,snapshot_id FROM universe_members ORDER BY rowid"
    ).fetchall()
    assert [dict(row) for row in members] == [{
        "id": "previous-healthy-member",
        "snapshot_id": previous.id,
    }]
    failed_run = db.execute(
        "SELECT status,snapshot_id,errors FROM universe_scanner_runs "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert failed_run["status"] == "failed"
    assert failed_run["snapshot_id"]
    assert failed_run["snapshot_id"] != previous.id
    assert failure_code in failed_run["errors"]
    assert db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0
    assert experiment_outcome_registrations == []


@spec_claim(
    SpecClaim(
        "UP-25",
        "universe_pipeline",
        "§3 retryable opportunity emission",
        "L2b",
        "When deterministic Opportunity emission escapes with any unhandled failure, "
        "UniverseService rolls back the attempted snapshot publication, keeps the previous "
        "healthy snapshot active, and records the failed run with the attempted snapshot id.",
        status="implemented",
    ),
)
def test_emission_evidence_failure_cannot_publish_partial_universe_refresh(
    tmp_path, monkeypatch
):
    _assert_emission_failure_cannot_publish_partial_universe_refresh(
        tmp_path, monkeypatch, "evidence"
    )


def test_unhandled_emitter_runtime_failure_cannot_publish_partial_universe_refresh(
    tmp_path, monkeypatch
):
    _assert_emission_failure_cannot_publish_partial_universe_refresh(
        tmp_path, monkeypatch, "runtime"
    )


def test_emission_failure_rolls_back_inside_existing_transaction(
    tmp_path, monkeypatch
):
    _assert_emission_failure_cannot_publish_partial_universe_refresh(
        tmp_path,
        monkeypatch,
        "evidence",
        caller_owns_transaction=True,
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral (direction inference + score consumption)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-01", "universe_pipeline", "§2 US-4 direction inference", "L2b",
              "_infer_direction has an 'event' branch: sharp 1h move → long/short, "
              "small move → None (mirrors score_event thresholds)"),
)
def test_infer_direction_event_branch():
    from aletheia.universe.signal_emitter import _infer_direction

    assert _infer_direction("event", {"return_1h": 9.0}) == "long"
    assert _infer_direction("event", {"return_1h": -9.0}) == "short"
    # Below the ±4% event threshold → no directional call.
    assert _infer_direction("event", {"return_1h": 2.0}) is None


@spec_claim(
    SpecClaim("UP-02", "universe_pipeline", "§2 US-1 scanner_weights consumed", "L2b",
              "compute_total_score multiplies opportunity_score by the operator's "
              "cfg.scanner_weights[family] (PI-adjustable weight is no longer inert)"),
)
def test_scanner_weights_scale_opportunity_score():
    from aletheia.universe.allocator import compute_total_score
    from aletheia.config.schema import UniverseConfig

    feat = {"tradability_score": 50.0, "quote_volume_24h": 1e12, "spread_bps": 2.0}
    sr = {"opportunity_score": 50.0, "scanner_family": "momentum"}

    base = compute_total_score(dict(feat), sr, UniverseConfig())  # weights={} → 1.0
    boosted = compute_total_score(
        dict(feat), sr, UniverseConfig(scanner_weights={"momentum": 2.0}),
    )
    suppressed = compute_total_score(
        dict(feat), sr, UniverseConfig(scanner_weights={"momentum": 0.1}),
    )
    assert boosted > base > suppressed


# ---------------------------------------------------------------------------
# L2a — Structural (same-source direction, market_type propagation, feature keying)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-03", "universe_pipeline", "§1.A US-4 same-source direction", "L2a",
              "forward-outcome (inserted_members) direction prefers scanner_direction "
              "— the SAME source the emitted opportunity uses — instead of "
              "re-inferring (which dropped direction to None for 54% of momentum)"),
)
def test_forward_outcome_direction_uses_scanner_direction():
    from aletheia.universe import service

    src = inspect.getsource(service)
    assert 'item.get("scanner_direction")' in src, (
        "inserted_members direction must prefer scanner_direction (same source as "
        "the emitted opportunity), not re-infer with _infer_direction alone"
    )


@spec_claim(
    SpecClaim("UP-04", "universe_pipeline", "§2 US-3 market_type propagation", "L2a",
              "inserted_members carries market_type so signal_emitter labels perp "
              "opportunities correctly (was fallback-'spot' for all)"),
)
def test_inserted_members_carry_market_type():
    from aletheia.universe import service

    src = inspect.getsource(service)
    assert '"market_type": item.get("market_type"' in src, (
        "inserted_members must include market_type; otherwise signal_emitter:70 "
        "falls back to 'spot' for every opportunity, mislabeling perp"
    )


@spec_claim(
    SpecClaim("UP-05", "universe_pipeline", "§2 US-8 feature keying", "L2a",
              "signal_emitter keys features by (symbol, market_type) so a symbol's "
              "spot and perp feature rows don't overwrite each other"),
)
def test_feature_lookup_uses_composite_key():
    from aletheia.universe import signal_emitter

    src = inspect.getsource(signal_emitter)
    assert "feat_by_key" in src and 'f.get("market_type"' in src, (
        "feature lookup must be keyed by (symbol, market_type), not symbol alone"
    )


# ---------------------------------------------------------------------------
# Observer side — key_observations exposure, per-symbol guards, scanner hint
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-06", "universe_pipeline", "§2 MO-1 key_observations to PI", "L2b",
              "PI layer1 exposes market_observer's key_observations (numeric "
              "citations); previously only a truncated thesis reached the PI"),
)
def test_key_observations_reach_pi():
    from aletheia.agent_shell.trading_context import _top_observations

    # Parser: JSON array → first n bullets; junk/empty → [].
    assert _top_observations('["1h RSI 68", "price > SMA20", "funding +0.03%"]', n=2) == [
        "1h RSI 68", "price > SMA20",
    ]
    assert _top_observations("") == []
    assert _top_observations(None) == []
    assert _top_observations("not json") == []

    # Structural: layer1 carries it AND the System Operator prompt renders it.
    from aletheia.agent_shell import trading_context, trading_decision
    assert '"key_observations": _top_observations' in inspect.getsource(trading_context)
    assert "key_observations" in inspect.getsource(trading_decision)


@spec_claim(
    SpecClaim("UP-07", "universe_pipeline", "§2 MO-2 per-symbol guard (断口F)", "L2a",
              "market_observer builds guard advisory PER-SYMBOL with scanner_family "
              "(the run-level symbol-LIST context never matched any guard)"),
)
def test_market_observer_per_symbol_guard():
    from aletheia.knowledge import router
    from aletheia.agents import market_observer

    assert hasattr(router, "build_guard_advisory")
    mo_src = inspect.getsource(market_observer)
    assert "build_guard_advisory" in mo_src
    assert '"scanner_family": scanner_family' in mo_src


@spec_claim(
    SpecClaim("UP-08", "universe_pipeline", "§2 MO-4 scanner_family in prompt", "L2a",
              "the market_observer LLM prompt names the scanner family that surfaced "
              "the symbol so the read validates that family, not a generic scan"),
)
def test_market_observer_prompt_scanner_hint():
    from aletheia.agents import market_observer

    src = inspect.getsource(market_observer)
    assert "scanner_hint" in src and "surfaced by the" in src


@spec_claim(
    SpecClaim("UP-09", "universe_pipeline", "§2 MO-3 F&G/top_movers populated", "L2a",
              "build_market_context_from_db populates MacroSnapshot.fng_value / "
              "fng_classification / top_movers_summary (collected + rendered but "
              "never read on the runtime path → the LLM always saw None)"),
)
def test_macro_context_populates_fng():
    from aletheia.data import context_builder

    src = inspect.getsource(context_builder.build_market_context_from_db)
    assert "fng_value=macro_row.get" in src
    assert "top_movers_summary=macro_row.get" in src


@spec_claim(
    SpecClaim("UP-10", "universe_pipeline", "§2 US-9 short hard-disable", "L2b",
              "when short_enabled=False, NO score overrides the regime gate for a "
              "short (previously score>=66 slipped through)"),
)
def test_short_disable_is_hard():
    from aletheia.universe.signal_emitter import _passes_regime_gate

    class _Regime:
        long_bias = 0.0
        short_bias = 0.0
        state = "bull"

    r = _Regime()
    # short_enabled=False → even a 99 score short is blocked
    assert _passes_regime_gate("short", 99.0, r, short_enabled=False) is False
    # short_enabled=True → high score still overrides (unchanged behavior)
    assert _passes_regime_gate("short", 99.0, r, short_enabled=True) is True
    # long is never affected by the short switch
    assert _passes_regime_gate("long", 99.0, r, short_enabled=False) is True


@spec_claim(
    SpecClaim("UP-19", "universe_pipeline", "§market-type snapshot eligibility", "L2b",
              "a candidate is execution-eligible only when a fresh snapshot exists for "
              "that exact spot/perp instrument; a same-symbol snapshot from the other "
              "market type must not pass the pre-trade freshness gate"),
)
def test_snapshot_freshness_is_market_type_scoped(monkeypatch):
    from datetime import datetime, timezone
    from aletheia.universe.snapshot_freshness import has_fresh_market_snapshot

    calls: list[dict] = []

    def snapshots(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(
        "aletheia.universe.snapshot_freshness.list_market_snapshots", snapshots,
    )
    assert has_fresh_market_snapshot(
        "BTC/USDT", "perp", now=datetime.now(timezone.utc),
    ) is False
    assert calls == [{"symbol": "BTC/USDT", "market_type": "perp", "limit": 1}]


@spec_claim(
    SpecClaim(
        "UP-21", "universe_pipeline", "§signal emission terminal evidence", "L2a",
        "signal_emitter exposes one terminal evidence writer so every selected universe "
        "member ends as emitted, reused, rejected, or error instead of silently disappearing.",
        status="implemented",
    ),
)
def test_signal_emitter_has_terminal_evidence_writer():
    from aletheia.universe import signal_emitter

    assert hasattr(signal_emitter, "_record_emission_decision")
    source = inspect.getsource(signal_emitter.emit_deterministic_opportunities)
    assert source.count("_record_emission_decision(") >= 8


@spec_claim(
    SpecClaim(
        "UP-20", "universe_pipeline", "§contract quote-volume normalization", "L2b",
        "When a linear contract ticker omits quoteVolume, Universe feature collection "
        "derives quote-currency volume from baseVolume × contractSize × last instead "
        "of dropping the enabled perp market as zero-volume.",
    ),
)
def test_contract_quote_volume_fallback_uses_contract_size():
    from aletheia.universe.features import _quote_volume_24h

    class Exchange:
        markets = {
            "BTC/USDT:USDT": {"contract": True, "contractSize": 0.01},
        }

    volume = _quote_volume_24h(
        Exchange(),
        "BTC/USDT:USDT",
        {"quoteVolume": None, "baseVolume": 9_000_000, "last": 60_000},
    )
    assert volume == 5_400_000_000.0


@spec_claim(
    SpecClaim("UP-11", "universe_pipeline", "§2 MO-6 single-symbol RS rank", "L2b",
              "cross-asset RS rank is omitted for <2 symbols (a lone 'Rank 1 of 1' "
              "reads to the LLM as 'strongest in market')"),
)
def test_rs_rank_omitted_for_single_symbol():
    from aletheia.trading.market_context import _render_relative_strength

    assert _render_relative_strength({"BTC/USDT": 5.0}) == ""
    multi = _render_relative_strength({"BTC/USDT": 5.0, "ETH/USDT": 2.0})
    assert "Rank" in multi


@spec_claim(
    SpecClaim("UP-12", "universe_pipeline", "§2 MO-8 crowding n/a", "L2b",
              "crowding renders 'n/a' when no funding/OI/LSR input exists, not "
              "'0/100' (which the LLM reads as 'not crowded')"),
)
def test_crowding_na_without_inputs():
    from aletheia.data.normalizer import compute_leverage_state
    from aletheia.data.context_builder import build_market_context_snapshot

    lv = compute_leverage_state("BTC/USDT")  # no funding/oi/lsr → crowding 0, no input
    out = build_market_context_snapshot(["BTC/USDT"], {}, {"BTC/USDT": lv}, macro=None)
    assert "0/100" not in out


@spec_claim(
    SpecClaim("UP-13", "universe_pipeline", "§2 MO-7 degraded fallback marked", "L2a",
              "the market_context hard-fallback labels itself DEGRADED so the LLM "
              "doesn't treat last-known figures as live source-of-truth"),
)
def test_fallback_context_marks_degraded():
    from aletheia.trading import market_context

    assert "DEGRADED" in inspect.getsource(market_context)


# ---------------------------------------------------------------------------
# UP-14 — UniverseMember persists all_scores (2A+.12) — round-trip, not string
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-14", "universe_pipeline", "§2A+.12 all_scores persisted", "L2b",
              "UniverseMember carries all_scores (5-family JSON) end-to-end: model "
              "field + migration column + service write + DB round-trip"),
)
def test_all_scores_persists_round_trip(tmp_path, monkeypatch):
    """all_scores(五家分数) 必须真落库并读得回,不是构造后被 model_dump 静默丢弃。

    用真实对象检查 + DB round-trip(不是 source 字符串 grep),避免 SP-S05 式假绿:
      1. model_fields 真含 all_scores(字段被删→立即挂)
      2. _ADDITIVE_COLUMNS 真注册 universe_members.all_scores(未注册→立即挂)
      3. insert → read back,all_scores JSON 完整往返(写路径断→立即挂)
    """
    import json
    import sqlite3

    # 1. 模型字段(真 introspection,非字符串)
    from aletheia.storage.models import UniverseMember
    assert "all_scores" in UniverseMember.model_fields, "UniverseMember 缺 all_scores 字段"

    # 2. migration 注册(真 list 成员判断,非字符串)
    from aletheia.storage.migrations import _ADDITIVE_COLUMNS
    cols = {(c, t) for c, t, _ in _ADDITIVE_COLUMNS}
    assert ("all_scores", "universe_members") in cols, (
        "_ADDITIVE_COLUMNS 未注册 universe_members.all_scores → 列不存在,model_dump 写库会丢"
    )

    # 3. DB round-trip
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    # FK not enforced on this raw connection (foreign_keys defaults OFF), so a
    # snapshot/registry row isn't required to exercise the all_scores round-trip.
    scores = {"momentum": 30.0, "mean_reversion": 0.0, "volatility": 12.0,
              "derivatives": 25.0, "event": 0.0, "short_bias": 18.0}
    member = UniverseMember(
        snapshot_id="snap_test", asset_id="asset_test", symbol="BTC/USDT",
        rank=1, total_score=42.0, scanner_family="momentum",
        all_scores=json.dumps(scores),
    )
    repo.insert_universe_member(member)

    row = conn.execute(
        "SELECT all_scores FROM universe_members WHERE id=?", (member.id,)
    ).fetchone()
    assert row is not None, "member 未落库"
    assert json.loads(row["all_scores"]) == scores, (
        f"all_scores 未完整往返: {row['all_scores']}"
    )


# ---------------------------------------------------------------------------
# UP-15 — score_volatility consumes 4h TF features (2A+.11) — behavioral, not grep
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-15", "universe_pipeline", "§2A+.11 4h TF volatility confirm", "L2b",
              "score_volatility reads atr_4h + bb_width_4h (collected by features.py "
              "but previously consumed by zero scorer): 4h ATR expansion / 4h BB "
              "squeeze raise the volatility score — multi-TF confirmation, not 1h noise"),
)
def test_score_volatility_consumes_4h_features():
    """atr_4h/bb_width_4h 必须真被 score_volatility 消费(行为差,非源码字符串):

      1. 基线 volatility setup(1h ATR+量能+方向)得分 base
      2. 追加 atr_4h 高(4h 持续放量)→ 分数 > base(4h 确认加分)
      3. 追加 bb_width_4h 窄(4h 挤压蓄能)→ 分数 > base
    反向验证:若 scorer 不读这两个字段,后两者恒等于 base → 立即挂。
    """
    from aletheia.universe.scanners import score_volatility

    # 基线:1h ATR 扩张 + 量能突破 + 4h 方向明确,但无 4h 确认字段
    base_feat = {
        "atr_pct": 5.5, "vol_ratio": 2.5, "return_4h": 3.0,
        "bb_width_pct": 4.0,
    }
    base_score, _, base_dir = score_volatility(dict(base_feat))
    assert base_dir == "long", "基线应产出方向(否则 score 被清零,无法比较)"

    # 4h ATR 持续放量 → 确认真实波动 regime,应加分
    atr4h_score, atr4h_reasons, _ = score_volatility({**base_feat, "atr_4h": 6.0})
    assert atr4h_score > base_score, (
        f"atr_4h 高未加分: {atr4h_score} !> {base_score} — score_volatility 未消费 atr_4h"
    )

    # 4h BB 挤压 → 高时间框架蓄能,应加分
    bb4h_score, bb4h_reasons, _ = score_volatility({**base_feat, "bb_width_4h": 2.0})
    assert bb4h_score > base_score, (
        f"bb_width_4h 窄未加分: {bb4h_score} !> {base_score} — score_volatility 未消费 bb_width_4h"
    )


# ---------------------------------------------------------------------------
# UP-16 — allocate_universe applies per-regime family quota (2A+.9) — behavioral
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-16", "universe_pipeline", "§2A+.9 regime-aware family quota", "L2b",
              "allocate_universe resolves family quota by regime: a bear regime "
              "gives short_bias/derivatives more slots than the static quota, and "
              "regime_state=None falls back to the static family_quota (backward compat)"),
)
def test_allocate_universe_regime_aware_quota():
    """per-regime family_quota 必须真影响分配(行为差,非源码 grep):

      1. resolve_family_quota(bear) 返回 bear 覆盖(short_bias 份额 > 静态)
      2. regime_state=None → 回退静态 family_quota(向后兼容)
      3. allocate_universe 在 bear regime 下,short_bias 候选获得的名额
         多于同一批候选在 static(None)下的名额
    """
    from aletheia.universe.allocator import resolve_family_quota, allocate_universe
    from aletheia.config.schema import UniverseConfig

    cfg = UniverseConfig(max_candidates=20, explore_ratio=0.0)

    # 1. bear 覆盖生效,short_bias 份额高于静态(静态里 short_bias 不在表,回退默认)
    bear_q = resolve_family_quota(cfg, "bear")
    assert bear_q.get("short_bias", 0) >= 0.25, f"bear 未提升 short_bias 份额: {bear_q}"

    # 2. None → 静态 family_quota(同一对象/等值)
    assert resolve_family_quota(cfg, None) == cfg.family_quota

    # 3. 端到端:20 个 momentum 候选。static quota momentum=0.30(→6 席),
    #    bear quota momentum=0.10(→2 席)——bear 应放行更少的 long-only momentum。
    scored = [
        {"symbol": f"C{i}/USDT", "scanner_family": "momentum",
         "total_score": 90.0 - i, "tradability_score": 50.0}
        for i in range(20)
    ]
    bear_sel = allocate_universe([dict(s) for s in scored], cfg, regime_state="bear")
    static_sel = allocate_universe([dict(s) for s in scored], cfg, regime_state=None)
    bear_mom = sum(1 for x in bear_sel if x.get("scanner_family") == "momentum")
    static_mom = sum(1 for x in static_sel if x.get("scanner_family") == "momentum")
    assert bear_mom < static_mom, (
        f"bear regime 未收紧 momentum 名额: bear={bear_mom} static={static_mom}"
    )


# ---------------------------------------------------------------------------
# UP-17 — explore phase respects family quota (no winner-take-all re-flood)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-17", "universe_pipeline", "§2 explore respects family quota", "L2b",
              "the explore phase of allocate_universe must NOT push a family past "
              "its quota: exploration draws only from families still under quota, so "
              "it adds diversity rather than re-flooding the winner-take-all family "
              "(root cause of 84% momentum despite a 30% quota, diagnosis 2026-07-03)"),
)
def test_explore_respects_family_quota():
    """行为断言(非源码 grep):winner-take-all 输入下,explore 不得让某 family 超配额。

    构造 12 个 momentum 候选(唯一 family)。exploit 阶段把 momentum 封在配额上限,
    余量进 remainder。旧行为:explore 从 remainder 随机补样 → 把已超配额的 momentum
    又塞回去(84% 偏斜真因)。新行为:explore 只从「仍在配额内」的 family 取样 →
    momentum 已达上限 → explore 不再追加 → 最终 momentum 数 ≤ 配额上限。
    """
    from aletheia.universe.allocator import allocate_universe
    from aletheia.config.schema import UniverseConfig

    cfg = UniverseConfig(
        max_candidates=10, explore_ratio=0.2,
        family_quota={"momentum": 0.30, "mean_reversion": 0.30, "volatility": 0.40},
    )
    scored = [
        {"symbol": f"M{i}/USDT", "scanner_family": "momentum",
         "total_score": 90.0 - i, "tradability_score": 60.0}
        for i in range(12)
    ]
    sel = allocate_universe([dict(s) for s in scored], cfg, regime_state=None)
    mom = sum(1 for x in sel if x.get("scanner_family") == "momentum")
    limit = max(1, int(cfg.max_candidates * cfg.family_quota["momentum"]))  # 3
    assert mom <= limit, f"explore re-flooded over-quota momentum: {mom} > {limit}"


# ---------------------------------------------------------------------------
# UP-18 — UniverseMember persists current regime context
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-18", "universe_pipeline", "§2A+.9 regime propagation", "L2b",
              "UniverseService writes the detected market regime into "
              "UniverseMember.regime_tag, and the repository persists it so "
              "member-layer learning is not stuck in the unknown bucket"),
)
def test_universe_member_regime_tag_persists_and_refresh_assigns(tmp_path, monkeypatch):
    """regime_tag must survive model_dump + insert and be assigned by refresh.

    The database migration already had universe_members.regime_tag, but the model
    omitted the field and refresh never populated it. That made recent member rows
    100% NULL even while market_regimes.state had live values.
    """
    import sqlite3

    from aletheia.storage.models import UniverseMember
    assert "regime_tag" in UniverseMember.model_fields, (
        "UniverseMember lacks regime_tag; model_dump will drop the regime context"
    )

    from aletheia.storage.migrations import _ADDITIVE_COLUMNS, run_migrations
    cols = {(c, t) for c, t, _ in _ADDITIVE_COLUMNS}
    assert ("regime_tag", "universe_members") in cols, (
        "_ADDITIVE_COLUMNS must keep universe_members.regime_tag available"
    )

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    member = UniverseMember(
        snapshot_id="snap_regime", asset_id="asset_regime", symbol="BTC/USDT",
        rank=1, total_score=42.0, scanner_family="momentum",
        regime_tag="sideways",
    )
    repo.insert_universe_member(member)

    row = conn.execute(
        "SELECT regime_tag FROM universe_members WHERE id=?", (member.id,)
    ).fetchone()
    assert row is not None, "member row was not inserted"
    assert row["regime_tag"] == "sideways", (
        f"regime_tag was not persisted: {row['regime_tag']!r}"
    )

    from aletheia.universe import service
    src = inspect.getsource(service.UniverseService.refresh)
    assert "regime_tag = current_regime.state if current_regime else None" in src
    assert "regime_tag=regime_tag" in src
    assert '"regime_tag": regime_tag' in src


@spec_claim(
    SpecClaim(
        "UP-24", "universe_pipeline", "§instrument-aware collector routing", "L2b",
        "Active universe market-data collection preserves venue and market type as an "
        "InstrumentId; a perp member must not be downgraded to a spot symbol.",
        status="implemented",
    ),
)
def test_observed_instruments_preserve_member_venue_and_market(monkeypatch):
    from types import SimpleNamespace

    from aletheia.agent_runtime import scheduler
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(
        repo,
        "get_active_universe_members",
        lambda limit: [
            {
                "asset_id": "asset-perp",
                "symbol": "SPCX/USDT",
                "market_type": "perp",
                "execution_eligible": 1,
            },
            {
                "asset_id": "asset-observe-only",
                "symbol": "AAPL/USDT",
                "market_type": "perp",
                "execution_eligible": 0,
            },
        ],
    )
    monkeypatch.setattr(
        repo,
        "get_universe_asset",
        lambda asset_id: {
            "venue": "okx",
            "market_type": "perp",
        },
    )
    cfg = SimpleNamespace(
        universe=SimpleNamespace(max_candidates=20),
        market_data=SimpleNamespace(
            ccxt_exchange="binance",
            observed_instruments=[],
            observed_symbols=[],
        ),
        exchange=SimpleNamespace(
            enabled_markets={"spot": True, "usdt_futures": True},
            preferred_market_type="auto",
        ),
    )

    instruments = scheduler._get_observed_instruments(cfg)

    assert [str(item) for item in instruments] == ["okx:SPCX/USDT:perp"]


@spec_claim(
    SpecClaim(
        "UP-22", "universe_pipeline", "§equity capability boundary", "L2c",
        "OKX instCategory=3 stock/ETF contracts remain observation-only until their "
        "asset-class and execution capability are explicitly integrated; crypto registry "
        "sync must exclude them.",
        status="implemented",
    ),
)
def test_okx_equity_contracts_are_excluded_from_crypto_registry():
    from aletheia.universe.registry import _is_excluded

    equity_swap = {
        "base": "SPCX",
        "id": "SPCX-USDT-SWAP",
        "info": {"instCategory": "3"},
    }
    crypto_swap = {
        "base": "BTC",
        "id": "BTC-USDT-SWAP",
        "info": {"instCategory": "1"},
    }

    assert _is_excluded(equity_swap) is True
    assert _is_excluded(crypto_swap) is False


@spec_claim(
    SpecClaim(
        "UP-23", "universe_pipeline", "§volume ranking consumption", "L2b",
        "Structured volume-ranking rows retain exchange and market_type when ordering "
        "registered universe assets; legacy plain-symbol snapshots remain spot-compatible.",
        status="implemented",
    ),
)
def test_volume_ranking_rows_preserve_instrument_identity():
    from aletheia.universe.service import UniverseService, _volume_ranked_instruments

    rows = [
        {"symbol": "SPCX/USDT", "market_type": "perp", "exchange": "okx"},
        "BTC/USDT",
        {"symbol": "ETH/USDT", "market_type": "perp", "exchange": "binance"},
    ]

    instruments = _volume_ranked_instruments(rows, default_venue="okx")

    assert [str(item) for item in instruments] == [
        "okx:SPCX/USDT:perp",
        "okx:BTC/USDT:spot",
    ]
    source = inspect.getsource(UniverseService.refresh)
    block = source[source.index("# Inject volume-ranking symbols"):source.index("features_list =")]
    assert "assets.sort" in block
    assert "upsert_universe_asset" not in block


# ---------------------------------------------------------------------------
# UP-24 — learning_candidate_key persists through universe_members round-trip
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "UP-24", "universe_pipeline", "§observer-learning outcome identity persistence", "L2b",
        "learning_candidate_key written during universe refresh must survive the "
        "insert→read-back cycle so market observer can carry the 4h outcome key "
        "into learning exposures after a process restart (#323).",
        status="implemented",
    ),
)
def test_learning_candidate_key_persists_round_trip(tmp_path, monkeypatch):
    """#323: universe_members.learning_candidate_key must be persisted.

    Three-layer check (mirrors test_all_scores_persists_round_trip):
      1. UniverseMember.model_fields contains learning_candidate_key
      2. _ADDITIVE_COLUMNS registers (learning_candidate_key, universe_members)
      3. insert → read-back: the key value survives the DB round-trip
    """
    import sqlite3

    from aletheia.storage.models import UniverseMember
    assert "learning_candidate_key" in UniverseMember.model_fields, (
        "UniverseMember missing learning_candidate_key field"
    )

    from aletheia.storage.migrations import _ADDITIVE_COLUMNS
    cols = {(c, t) for c, t, _ in _ADDITIVE_COLUMNS}
    assert ("learning_candidate_key", "universe_members") in cols, (
        "_ADDITIVE_COLUMNS does not register universe_members.learning_candidate_key "
        "— column won't exist after migration, model_dump write will fail silently"
    )

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    candidate_key = "BTC/USDT:spot:4h:2026-07-22T12:00:00+00:00"
    member = UniverseMember(
        snapshot_id="snap-323", asset_id="asset-btc", symbol="BTC/USDT",
        rank=1, total_score=75.0, scanner_family="momentum",
        learning_candidate_key=candidate_key,
    )
    repo.insert_universe_member(member)

    row = conn.execute(
        "SELECT learning_candidate_key FROM universe_members WHERE id=?", (member.id,)
    ).fetchone()
    assert row is not None, "member not persisted"
    assert row["learning_candidate_key"] == candidate_key, (
        f"learning_candidate_key not round-tripped: got {row['learning_candidate_key']!r}"
    )


# ---------------------------------------------------------------------------
# UP-26 — live-duplicate suppression prevents scanner flooding (#326)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "UP-26", "universe_pipeline", "§signal emission live-duplicate guard (#326)", "L2b",
        "emit_deterministic_opportunities must suppress a new signal for symbol+direction "
        "when a live (open/queued, non-expired) opportunity for the same symbol+direction+market "
        "already exists in the evaluation queue. Without this, fast-signalling symbols emit "
        "a new slot-stealing candidate every 10 min while prior ones are still pending (#326).",
        status="implemented",
    ),
)
def test_live_duplicate_suppressed_when_active_opportunity_exists(tmp_path, monkeypatch):
    """#326: a second emission for the same symbol+direction must be suppressed.

    Two calls to emit_deterministic_opportunities with identical symbol+direction:
    - First call: no live opportunity exists → emits 1 opportunity.
    - Second call (different snapshot_id): live opportunity still open → suppressed.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    import aletheia.storage.repositories as repo
    from aletheia.storage.migrations import run_migrations

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "live_dup_test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    from aletheia.universe.signal_emitter import emit_deterministic_opportunities
    from aletheia.storage.models import UniverseSnapshot, UniverseMember
    from aletheia.config.loader import load_config

    cfg = load_config()
    cfg.universe.enabled = True
    cfg.regime.enabled = False
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    cfg.market_data.ccxt_exchange = "binance"

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=60)).isoformat()

    def _make_member(snapshot_id: str) -> tuple[dict, dict]:
        snap = UniverseSnapshot(
            id=snapshot_id, exchange="binance", market_type="spot",
            universe_size=1, created_at=now.isoformat(),
        )
        repo.insert_universe_snapshot(snap)
        member = UniverseMember(
            snapshot_id=snapshot_id, asset_id=f"asset-{snapshot_id}",
            symbol="SNDK/USDT", rank=1, total_score=75.0,
            scanner_family="momentum", expires_at=expires_at,
        )
        repo.insert_universe_member(member)
        feat = {
            "asset_id": f"asset-{snapshot_id}",
            "symbol": "SNDK/USDT",
            "venue": "binance",
            "market_type": "spot",
            "last_price": 50.0,
            "quote_volume_24h": 100_000_000.0,
            "spread_bps": 5.0,
            "execution_eligible": True,
            "scanner_family": "momentum",
            "scanner_direction": "short",
            "reasons": ["RS24h -6.0% vs BTC"],
            "total_score": 75.0,
            "return_4h": -4.0,
            "relative_strength_24h": -6.0,
        }
        return [member.model_dump()], [feat]

    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.lookup_factor_signal", lambda _: {}
    )
    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.has_fresh_market_snapshot", lambda *a, **kw: True
    )

    members1, feats1 = _make_member("snap-flood-1")
    created1 = emit_deterministic_opportunities(
        members1, "snap-flood-1", feats1
    )
    assert created1 == 1, f"first emission should create 1 opportunity, got {created1}"

    members2, feats2 = _make_member("snap-flood-2")
    created2 = emit_deterministic_opportunities(
        members2, "snap-flood-2", feats2
    )
    assert created2 == 0, (
        f"second emission must be suppressed (live_duplicate_suppressed) — got {created2}; "
        "the live-duplicate guard at signal_emitter.py is missing or bypassed"
    )

    # Confirm the suppression reason is recorded in filter_decisions.
    rows = conn.execute(
        "SELECT reason_code FROM filter_decisions "
        "WHERE symbol='SNDK/USDT' AND filter_name='opportunity_emission' "
        "AND decision='rejected' ORDER BY evaluated_at DESC LIMIT 5"
    ).fetchall()
    reason_codes = {r["reason_code"] for r in rows}
    assert "live_duplicate_suppressed" in reason_codes, (
        f"expected live_duplicate_suppressed in filter_decisions; got {reason_codes}"
    )
