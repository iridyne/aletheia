"""Config schema using Pydantic v2 models."""

import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_logger = logging.getLogger("aletheia.config")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class AppConfig(BaseModel):
    name: str = "Aletheia Trading OS"
    dashboard_port: int = 8787
    auto_open_browser: bool = True
    bind_host: str = "127.0.0.1"
    # Overview Equity Curve semantics when a time range (7D/30D/90D) is selected:
    #   full_cumulative  — every point's Y is the all-time cumulative realized PnL;
    #                      the range only narrows the visible window, so the end
    #                      point never changes between ranges (standard equity
    #                      curve; the default).
    #   window_relative  — cumulative restarts from 0 at the window's first trade,
    #                      so the end point shows "PnL earned within this window"
    #                      (differs across ranges; useful for period attribution).
    equity_curve_mode: str = "full_cumulative"

    @field_validator("bind_host")
    @classmethod
    def validate_bind_host(cls, v: str) -> str:
        if v not in _LOOPBACK_HOSTS:
            _logger.warning("bind_host=%s is not loopback — API will be network-exposed without authentication", v)
        return v

    @field_validator("equity_curve_mode")
    @classmethod
    def validate_equity_curve_mode(cls, v: str) -> str:
        if v not in ("full_cumulative", "window_relative"):
            return "full_cumulative"
        return v


class RuntimeConfig(BaseModel):
    gateway_mode: str = "background"
    scheduler_enabled: bool = True
    paused: bool = False
    # Global trading brake. When true, the risk engine rejects every proposal
    # (paper and live). Set by human action or auto-tripped on daily-loss limit.
    kill_switch_active: bool = False
    # Git versioning of data/knowledge, data/playbooks, data/reports.
    git_enabled: bool = True
    # Hot-reload the inner uvicorn worker when files under aletheia/ change.
    # True = any Claude session that edits a .py file gets the change live without
    # a manual `aletheia gateway restart`. The supervisor PID does not change, so
    # single-instance mutex (pidfile) is preserved. Set False before going live.
    auto_reload: bool = True
    # Experimental PI runtime continuity. When false, the legacy update/gateway
    # behavior stays the default; operator routes can still inspect recorded state.
    # Automatically register the Windows Task Scheduler watchdog on gateway start.
    autostart_on_start: bool = True
    runtime_continuity_enabled: bool = False
    runtime_continuity_lock_ttl_sec: int = 300
    runtime_continuity_shadow_port_offset: int = 100


class ExchangeConfig(BaseModel):
    name: str = "bitget"
    mode: str = "demo"
    enabled_markets: dict[str, bool] = Field(default_factory=lambda: {"spot": True, "usdt_futures": True})
    # When both spot and usdt_futures are enabled, which market_type to emit per
    # canonical symbol in the legacy observed_instruments_from_config path.
    # "auto" → perp (Aletheia primary trading instrument; short requires perp,
    # long defaults to perp). "spot" | "perp" → always that type. Prevents
    # market_observer from running the LLM twice for the same ETH (redundant
    # 76% vs 74% spot vs perp directional read — price is the same). Dedup
    # OD-S01 locks this against regression.
    preferred_market_type: str = "auto"
    # Symbol list — semantics determined by risk.symbol_filter_mode:
    #   "whitelist" → only these may trade; "blacklist" → these are blocked.
    symbol_list: list[str] = Field(default_factory=list)
    # Where autonomous paper trades execute:
    #   "okx_demo"        → OKX simulated trading via CCXT (auto-filtered symbol set).
    #   "binance_testnet" → Binance Futures Testnet via CCXT (556 pairs, highest coverage).
    #   "bitget_demo"     → Bitget demo/simulated-trading API (paptrading header,
    #                       USDT-futures only, 42 pairs). Requires mode == "demo".
    #   "paper_internal"  → local simulated fills (no exchange call).
    paper_execution_mode: str = "bitget_demo"
    # Futures position mode: "hedge" (long/short separate) or "one_way".
    position_mode: str = "hedge"
    # Explicit perp symbol allowlist. Empty = all perps passing universe registry.
    futures_symbols: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    )


class MarketDataConfig(BaseModel):
    # "ccxt" uses CCXT Binance/Bybit; "bitget" falls back to BitgetClient (legacy).
    provider: str = "ccxt"
    ccxt_exchange: str = "binance"
    ccxt_fallback: str = "bybit"
    # Symbols to observe for market analysis (canonical BTC/USDT format).
    # Kept for backwards compatibility — if observed_instruments is non-empty it takes
    # precedence and this list is derived from it automatically.
    observed_symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    # Full instrument identifiers: "exchange:BASE/QUOTE:market_type"
    # e.g. ["binance:BTC/USDT:spot", "binance:BTC/USDT:perp", "bybit:ETH/USDT:spot"]
    # When non-empty, this is the authoritative source for what to observe.
    # When empty, observed_symbols + ccxt_exchange + "spot" are used (legacy path).
    observed_instruments: list[str] = Field(default_factory=list)
    ohlcv_cache_ttl_min: int = 10
    # Append macro (SPY/VIX/DXY/Gold) block to LLM market context prompt.
    enable_yfinance_macro: bool = False
    # Round 2 macro signals (Step 1-4 zero-cost data integration).
    # Binance Futures globalLongShortAccountRatio; populates
    # OpenInterestSnapshot.long_short_ratio which compute_leverage_state already
    # consumes into crowding_score (normalizer.py:73-77).
    enable_long_short_ratio: bool = True
    # Alternative.me Fear & Greed Index — single value 0-100 updated ~daily.
    # Fills MacroSnapshot.fng_value + fng_classification; renders into the macro
    # block of enriched_context. Failure is degraded (other macro fields preserved).
    enable_fear_greed: bool = True
    # CoinGecko top-N markets — universe-scale price/change snapshots every Phase B
    # tick (~10 min). Builds a "Top Gainers / Top Losers" summary string folded
    # into MacroSnapshot.top_movers_summary. No API key (public free tier).
    enable_coingecko_top: bool = True
    coingecko_top_n: int = 100  # CoinGecko hard ceiling is 250 per_page
    # On startup / sparse-window detection, backfill derivatives history (funding +
    # OI) up to this many hours so the LLM sees trend, not a single point. 0 = off.
    derivatives_history_hours: int = 24
    # Hyperliquid public info API (zero key) — cross-venue perp OI + funding for
    # observed symbols, tagged exchange="hyperliquid" in the funding/OI tables.
    # Excluded from the PRIMARY per-symbol read (so it never pollutes the venue
    # the LLM sees); kept as queryable cross-venue context. Phase B cadence (~10m).
    enable_hyperliquid: bool = True
    # DefiLlama public API (zero key) — total stablecoin mcap / chain TVL / DEX
    # volume, stored in onchain_snapshots and folded into the macro context block.
    # Fills the on-chain hole left by the never-built Glassnode. Phase B cadence.
    enable_defillama: bool = True
    # Master switch for prediction market observation (Polymarket). When False,
    # prediction_observer does not run and the Prediction Lens page shows disabled.
    prediction_enabled: bool = False
    # Polymarket category slugs (or keywords) to observe. prediction_observer
    # fetches active markets matching these categories every 30 minutes.
    observed_prediction_markets: list[str] = Field(default_factory=lambda: ["crypto", "politics"])
    # US equities to observe — canonical Yahoo Finance tickers (e.g. "AAPL").
    # equity_observer fetches daily bars via yfinance AND real-time price via
    # exchange stock tokens (r-series + ON-series on Bitget; future OKX SWAP).
    # Runs Mon–Fri 14:30–21:00 UTC (US market hours). Empty = disabled.
    observed_equities: list[str] = Field(default_factory=list)
    # Deprecated: use observed_equities instead. Kept for backwards compat.
    observed_stocks: list[str] = Field(default_factory=list)
    # Tokenized equity/ETF spot symbols on any supported exchange (Bitget ON-series,
    # Bitget r-series, future OKX SWAP).  Canonical form e.g. "rNVDA/USDT",
    # "AAPLON/USDT".  equity_observer uses these for real-time price cross-validation.
    # Replaces the narrower observed_xstocks (which remains a backward-compat alias).
    # Default covers the major r-series + ON-series starters; edit in Settings UI.
    observed_stock_tokens: list[str] = Field(
        default_factory=lambda: [
            "rNVDA/USDT", "rTSLA/USDT", "rAAPL/USDT", "rAMD/USDT",
            "rMSFT/USDT", "rGOOGL/USDT", "rAMZN/USDT", "rMETA/USDT",
            "AAPLON/USDT", "TSLAON/USDT", "NVDAON/USDT", "SPYON/USDT",
        ]
    )
    # Deprecated: use observed_stock_tokens instead.
    observed_xstocks: list[str] = Field(default_factory=list)
    # Third-tier fallback: CoinGecko + Binance public API when CCXT primary+fallback both fail.
    enable_crypto_fallback: bool = True
    # Coinalyze cross-exchange OI + liquidation aggregator (requires API key).
    # Replaces the dead CoinGlass/CCXT/Bitget-OI paths. Free tier: 100 req/day.
    # Set coinalyze_api_key in secrets to enable; gracefully skips if absent.
    enable_coinalyze: bool = True
    coinalyze_api_key: str | None = None
    # Cross-source price divergence detection.
    price_divergence_threshold_pct: float = 2.0
    price_divergence_alert_enabled: bool = True
    cross_source_check_enabled: bool = False


class LiveConfig(BaseModel):
    state: str = "live_disabled"
    first_live_confirmation_done: bool = False


class AgentConfig(BaseModel):
    permission_mode: str = "Paper"
    autonomous_learning: bool = True
    # When true, suppress historical performance stats from the System Operator prompt
    # so the LLM cannot trigger self-protection based on past WR/PnL.
    # Intended for demo/test phases where pipeline throughput matters more than
    # performance-based gating. Re-disable before evaluating real results.
    bypass_distress_gate: bool = False


class OperatorConfig(BaseModel):
    enabled: bool = True
    bind_host: str = "127.0.0.1"
    port: int = 8791
    permission_mode: str = "ReadOnly"
    heartbeat_interval_sec: int = 10
    heartbeat_stale_after_sec: int = 45
    restart_after_sec: int = 90
    max_restart_failures: int = 3
    loop_enabled: bool = True
    main_session_id: str = "operator-main"

    @field_validator("bind_host")
    @classmethod
    def validate_bind_host(cls, v: str) -> str:
        if v not in _LOOPBACK_HOSTS:
            _logger.warning("operator.bind_host=%s is not loopback — operator API will be network-exposed", v)
        return v


class ProviderConfig(BaseModel):
    type: str = ""
    base_url: str = ""
    protocol: str = "chat_completions"
    model: str = "default"
    enabled: bool = True
    command: str = ""
    mode: str = ""
    api_key_secret_ref: str | None = None
    codex_model: str | None = None
    workspace_url: str | None = None
    # Auth header for anthropic_compatible: "x-api-key" (native Anthropic, default)
    # or "bearer" (gateway-fronted endpoints e.g. Databricks AI Gateway).
    auth_scheme: str = "x-api-key"
    # OAuth subscription auth (openai_native / anthropic_native)
    auth_type: str = "api_key"  # "api_key" | "oauth" | "none"
    oauth_token_ref: str | None = None
    oauth_refresh_ref: str | None = None
    oauth_expires_at: int | None = None  # Unix ms timestamp
    # Request-level model fallback. When the configured model returns a
    # transient capacity/rate-limit error, the runtime can try these models
    # before giving up. If empty, the runtime may use list_models() detection as
    # a best-effort catalogue. This never changes the persisted default model.
    model_fallback_enabled: bool = True
    fallback_models: list[str] = Field(default_factory=list)


class ModelsConfig(BaseModel):
    default_provider: str = "openai_compatible"
    # Two-tier model routing (MR, 2026-07-01): let top-level decision agents use a
    # stronger model while bulk data-extraction agents use a cheaper/faster one.
    # None → fall back to default_provider, so existing configs need no migration.
    deep_provider: str | None = None   # decision/analysis tier: trading_decision, strategy_designer, risk_critic, …
    fast_provider: str | None = None   # extraction tier: research_worker bulk LLM calls
    providers: dict[str, ProviderConfig] = Field(default_factory=lambda: {
        "openai_compatible": ProviderConfig(
            type="openai_compatible",
            base_url="http://localhost:8000/v1",
            protocol="chat_completions",
            model="default",
        ),
        "anthropic_compatible": ProviderConfig(
            type="anthropic_compatible",
            base_url="http://localhost:8001",
            model="default",
        ),
        "codex_cli": ProviderConfig(
            type="codex_cli",
            enabled=False,
            command="codex",
            mode="exec",
        ),
        "claude_cli": ProviderConfig(
            type="claude_cli",
            enabled=False,
            command="claude",
            mode="print",
        ),
    })


class RiskConfig(BaseModel):
    # Kill switch auto-trigger: when enabled, breaching max_daily_loss_pct trips
    # the global trading brake automatically. Separate toggles for demo and live.
    kill_switch_enabled_live: bool = True
    kill_switch_enabled_demo: bool = False
    max_daily_loss_pct: float = 1.0
    max_single_trade_risk_pct: float = 0.25
    max_total_live_exposure_pct: float = 5.0
    max_live_position_notional_usdt: float = 50.0
    max_live_limited_cap_usdt: float = 100.0
    # Paper/demo bounds — promotion-gate evidence is built from paper trades, so
    # paper orders must NOT be unbounded (guide §21.3). Distinct tokens keep demo
    # size rejections out of the live-critical-violation count.
    starting_capital_usdt: float = 100.0
    max_demo_position_notional_usdt: float = 200.0
    # Per-symbol cumulative notional cap (CUM-ADD). Same-symbol+direction adds
    # average-merge into ONE position, so repeated adds can grow a position past the
    # single-trade cap. This bounds TOTAL open notional per symbol (across positions,
    # both demo and live). 0 disables. Set above the single-position caps so it only
    # catches genuine runaway accumulation, not a normal first entry.
    max_per_symbol_notional_usdt: float = 400.0
    max_demo_single_trade_risk_pct: float = 2.0
    max_futures_leverage_live: int = 2
    max_futures_leverage_demo: int = 5
    # Per-asset-tier leverage limits (major=BTC/ETH, mid=SOL/BNB/XRP, alt=others)
    max_leverage_major_demo: int = 5
    max_leverage_major_live: int = 3
    max_leverage_mid_demo: int = 3
    max_leverage_mid_live: int = 2
    max_leverage_alt_demo: int = 2
    max_leverage_alt_live: int = 1
    # Account-level aggregate leverage cap: sum(notional*leverage)/equity
    max_account_leverage_demo: float = 3.0
    max_account_leverage_live: float = 1.5
    # Margin monitoring thresholds
    futures_margin_warning_ratio: float = 0.70
    futures_margin_force_close_ratio: float = 0.85
    # Liquidation buffer: reject if est. liq price within X% of entry
    min_liquidation_buffer_pct: float = 5.0
    max_open_positions_live: int = 3
    max_open_positions_demo: int = 6
    # "whitelist" = only symbol_list may trade; "blacklist" = all except symbol_list.
    symbol_filter_mode: str = "blacklist"
    symbol_list: list[str] = Field(default_factory=list)
    allowed_exchange: str = "bitget"
    allow_short_demo: bool = True
    allow_short_live: bool = True
    require_risk_check_for_all_orders: bool = True
    # Portfolio correlation guard (check #16)
    correlation_guard_enabled: bool = True
    max_sector_concentration_pct: float = 60.0
    max_portfolio_beta: float = 3.0
    # Scanner-family live-eligibility gate (check #14, Position C 2026-07-02).
    # Replaces the deleted strategy-FSM eligibility gate: a LIVE trade needs its
    # scanner_family (in the proposal's direction) to show a proven clean-window
    # edge — at least N settled forward outcomes AND win_rate >= threshold.
    live_family_min_samples: int = 30
    live_family_min_win_rate_pct: float = 50.0


class TelegramConfig(BaseModel):
    enabled: bool = False
    allowed_chat_ids: list[int] = Field(default_factory=list)
    notify_trade_fills: bool = True
    notify_daily_summary: bool = True
    daily_summary_hour_cst: int = 8
    dnd_enabled: bool = False
    dnd_start_hour: int = 23
    dnd_end_hour: int = 7


class WebhookConfig(BaseModel):
    enabled: bool = False
    url: str = ""
    secret: str = ""
    notify_trade_fills: bool = True
    notify_alerts: bool = True
    notify_daily_summary: bool = True
    timeout_sec: float = 10.0


class HealthConfig(BaseModel):
    enabled: bool = True
    alert_via_telegram: bool = True
    alert_via_windows_toast: bool = True
    max_agent_failure_ratio: float = 0.5
    llm_unavailable_alert_after: int = 3
    # Silent-degradation guard: flag the market-data feed as stale when the newest
    # snapshot is older than this (market data is collected every 5 min).
    market_data_max_staleness_min: int = 30
    # Gateway supervisor (process self-healing).
    supervisor_max_restarts: int = 5
    supervisor_restart_window_sec: int = 600
    supervisor_backoff_cap_sec: int = 60
    # --- Deterministic self-heal: the health-check job ACTS on the signals it
    # already computes (NO LLM). Closes the gap where /api/health flagged stale
    # code for 11h but nothing consumed the signal to restart.
    self_heal_enabled: bool = True            # master toggle
    # min_interval (30m) is deliberately > supervisor_restart_window_sec (600s):
    # two self-heal restarts can never fall inside one supervisor window, so
    # self-heal alone can never exhaust the supervisor's give-up budget.
    self_heal_min_interval_sec: int = 1800    # >= 30 min between any two self-heal restarts
    self_heal_max_per_hour: int = 2           # hard ceiling on self-heal restarts/hour
    self_heal_code_drift: bool = True         # restart on code_updated_pending_restart (self-terminating)
    self_heal_market_data: bool = True        # restart on market_data_stale (rate-limited; external root cause)
    self_heal_port_cleanup: bool = True       # clean orphan/port-holder PIDs (cleanup, not restart)
    # LLM-unavailable / kill_switch / agent_failure are alert-only by design —
    # a restart cannot fix an external outage, human kill switch, or bad strategy.
    # Fail-loud LLM startup gate: at boot, probe every enabled provider with a REAL
    # inference call; if none can actually complete one, pause the trading loop
    # (runtime.paused=True) instead of running blind. The process stays ALIVE (it is
    # NOT killed — that would just trigger a supervisor/watchdog respawn loop); the
    # Web UI shows the degraded state and the operator resumes after fixing a provider.
    require_llm_at_startup: bool = True
    startup_gate_max_retries: int = 3
    startup_gate_retry_delay_sec: int = 15
    # Auto-resume: when startup gate pauses the system, periodically probe LLM
    # and auto-resume when connectivity is restored.
    auto_resume_enabled: bool = True
    auto_resume_interval_sec: int = 300
    # Stall detection: after this many consecutive failed probes, escalate to
    # operator with root-cause classification (secret_missing vs network vs auth).
    auto_resume_stall_threshold: int = 6
    auto_resume_stall_backoff_factor: float = 2.0
    auto_resume_max_interval_sec: int = 1800
    # Exchange connectivity monitor: detect network outages and auto-pause/resume.
    exchange_probe_interval_sec: int = 30
    exchange_probe_failure_threshold: int = 3
    exchange_auto_resume_interval_sec: int = 60
    exchange_auto_resume_success_threshold: int = 2
    exchange_stall_threshold: int = 30


class RetentionConfig(BaseModel):
    # Telemetry pruning (days). Trust-bearing records are NEVER pruned.
    agent_runs_days: int = 30
    tool_calls_days: int = 30
    run_steps_days: int = 30
    market_snapshots_days: int = 30
    portfolio_snapshots_days: int = 90
    jsonl_max_mb: float = 50.0
    # Opportunities older than this that are still 'open' are marked 'expired'.
    # Prevents indefinite accumulation of stale signals (default: 48h).
    opportunity_ttl_hours: int = 48


class MarketAwareExitsConfig(BaseModel):
    """Market-aware exit intelligence for position_manager."""
    enabled: bool = False
    min_hold_hours_before_market_exit: float = 0.5
    max_signal_age_min: float = 35.0
    urgent_exit_score: int = 80
    urgent_exit_max_pnl_pct: float = -0.3
    tighten_stop_score: int = 60
    confidence_collapse_score: int = 30
    tighten_atr_mult: float = 0.8
    tighten_pct: float = 0.8


class PersonaParam(BaseModel):
    """One Operator-decidable sizing parameter as a bounded range [min, init, max].

    Persona supplies the range; the System Operator decides the actual value WITHIN
    it (P2+, context-aware); the risk-engine hard caps remain the outer envelope the
    range can never exceed. For P1 the effective value is `init`.
    See docs/design/dynamic_sizing_operator_persona_design.md.
    """
    min: float
    init: float
    max: float


class TradingPersona(BaseModel):
    """A named 'trading style' = a bundle of Operator-decidable sizing bounds.

    `params` maps a param name → its [min,init,max] range. `operator_decidable`
    marks which params the Operator may move within range (P2+); others stay pinned
    at init. Recognised param keys (P1): notional_usdt, kelly_fraction, leverage_max,
    per_trade_loss_pct, max_adds, add_fraction.
    """
    name: str
    description: str = ""
    params: dict[str, PersonaParam] = Field(default_factory=dict)
    operator_decidable: dict[str, bool] = Field(default_factory=dict)
    # Progressive-unlock ladder (P4): the ORDER params are unfrozen in when
    # persona_progressive_unlock_enabled is on. Position i unlocks only after the
    # earlier ones and enough clean samples accrue; distress refreezes all. Empty =
    # never progressively unlock this persona (use the static operator_decidable).
    unlock_ladder: list[str] = Field(default_factory=list)


def _default_personas() -> dict:
    """Built-in personas. `conservative` init values equal the current flat-config
    defaults so enabling persona sizing with the default persona is a ZERO behavior
    change. `balanced`/`roll` widen the ranges (roll = small base + high leverage +
    pyramid, per owner's 滚仓 brief). All ranges stay inside the risk-engine hard
    envelope; the Operator/Learning can never exceed the envelope regardless.
    """
    def P(mn, i, mx):
        return PersonaParam(min=mn, init=i, max=mx)
    return {
        "conservative": TradingPersona(
            name="conservative",
            description="现状档:窄区间,init=当前扁平配置(启用后零行为变化)",
            params={
                "notional_usdt": P(5, 25, 100),      # == default_notional_usdt
                "kelly_fraction": P(0.10, 0.25, 0.40),  # == vol_kelly_cap_fraction
                "leverage_max": P(1, 5, 5),
                "per_trade_loss_pct": P(1.0, 2.0, 2.0),  # == default_stop_loss_pct
                "max_adds": P(0, 0, 0),
                "add_fraction": P(0.0, 0.0, 0.0),
            },
            operator_decidable={},  # P1: all pinned at init
        ),
        "balanced": TradingPersona(
            name="balanced",
            description="温和放大:更大基准与温和加仓上限",
            params={
                "notional_usdt": P(25, 200, 500),
                "kelly_fraction": P(0.15, 0.35, 0.50),
                "leverage_max": P(1, 5, 5),
                "per_trade_loss_pct": P(1.0, 2.0, 3.0),
                "max_adds": P(0, 0, 1),
                "add_fraction": P(0.0, 0.0, 0.5),
            },
            operator_decidable={},
            unlock_ladder=["notional_usdt", "per_trade_loss_pct", "kelly_fraction"],
        ),
        "roll": TradingPersona(
            name="roll",
            description="滚仓:小本金+高杠杆+金字塔加浮盈+极紧止损,博超额(高方差)",
            params={
                # Grounded initial values (owner + operator, 2026-07-02): 1% single-
                # trade loss (classic 1% rule), leverage init 10x / HARD CAP 15x (20x
                # buffer too thin), fractional Kelly ≤0.5, ≤3 pyramid adds. NOTE: the
                # risk-engine demo leverage cap (max_futures_leverage_demo=5) still
                # clamps this until the owner raises it (a separate trust-boundary call).
                "notional_usdt": P(20, 50, 200),
                "kelly_fraction": P(0.10, 0.25, 0.50),
                "leverage_max": P(5, 10, 15),
                "per_trade_loss_pct": P(0.5, 0.75, 1.0),  # 极紧,max 1% = 单笔亏损铁律
                "max_adds": P(0, 3, 3),
                "add_fraction": P(0.0, 0.5, 1.0),
            },
            operator_decidable={},
            # 滚仓解冻顺序:先仓位%→杠杆→止损→加仓→kelly(影响可控→影响大)。
            unlock_ladder=["notional_usdt", "leverage_max", "per_trade_loss_pct",
                           "max_adds", "kelly_fraction"],
        ),
    }


class TradingConfig(BaseModel):
    # Paper position exit rules used by the position_manager. Non-zero defaults so
    # the autonomous loop actually closes positions and realizes PnL.
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 4.0
    max_position_hold_hours: float = 72.0
    # Edge Lab: realistic paper fill simulation
    slippage_bps: float = 5.0        # basis points of adverse slippage on paper fills
    execution_fee_pct: float = 0.06  # % fee per side — Bitget taker (0.06%); change to match your live exchange
    # Minimum LLM confidence for paper_trader to open a position
    # (high | medium | speculative). Below this, the opportunity is recorded but
    # not traded.
    min_confidence_to_trade: str = "medium"
    # Account-wide position adoption ("接管即纳管"). When ON, the periodic
    # reconcile job AUTO-ADOPTS external DEMO positions (positions on the demo
    # account that Aletheia did not open) into local management, and
    # reconcile-closes demo orphans. Owner decision 2026-06-12: DEFAULT ON — the
    # mandate is unified AI management of the whole account, so leaving externals
    # unmanaged would defeat the point. This flag is a KILL-SWITCH to DISABLE the
    # behavior, not an opt-in. ⚠️ Consequence when ON: pre-existing demo positions
    # get default stops/TP from position_manager and MAY be auto-closed. LIVE
    # positions are NEVER auto-adopted regardless of this flag (human-confirmed only).
    auto_adopt_enabled: bool = True
    # Volatility-target sizing: normalize risk per trade to ATR
    default_notional_usdt: float = 25.0
    vol_sizing_enabled: bool = True
    vol_target_risk_usdt: float = 25.0
    vol_target_pct: float = 1.5
    vol_kelly_cap_fraction: float = 0.25
    # Trailing stop / breakeven
    trail_enabled: bool = False
    trail_atr_mult: float = 1.5
    trail_pct: float = 1.0
    breakeven_trigger_pct: float = 1.5
    # Exchange-native TPSL bracket orders (Bitget demo/live only). Default off —
    # enable after verifying Bitget TPSL API behavior in demo mode.
    exchange_tpsl_enabled: bool = False
    # Market-aware exit intelligence: uses Market Observer signals to dynamically
    # tighten stops or exit early when market conditions deteriorate.
    market_aware_exits: "MarketAwareExitsConfig" = Field(default_factory=lambda: MarketAwareExitsConfig())
    # --- Trading personas (dynamic-sizing design, P1) ---
    # When persona_sizing_enabled is False (DEFAULT), sizing uses the flat fields
    # above (default_notional_usdt / vol_kelly_cap_fraction / ...) → ZERO behavior
    # change. When True, sizing reads the ACTIVE persona's params (P1: their init).
    # The Operator/Learning that decides values WITHIN each persona range arrives in
    # P2/P3; the risk-engine hard caps stay the immutable outer envelope regardless.
    persona_sizing_enabled: bool = False
    active_persona: str = "conservative"
    personas: dict[str, TradingPersona] = Field(default_factory=_default_personas)
    # --- Persona learning (P3): the Operator's decision space as a learnable policy ---
    # When enabled, the sizing risk_appetite is nudged by the historical clean-window
    # reward of the current context bucket (regime). Default OFF (dormant). The learner
    # self-refreezes (returns no adjustment) when it lacks clean samples or its own
    # recent policy is degrading — a soft circuit-breaker, no config flip needed.
    persona_learning_enabled: bool = False
    persona_learning_min_samples: int = 15
    # --- Progressive unlock (P4): unfreeze operator-decidable params one at a time ---
    # When enabled, a persona's unlock_ladder params become operator_decidable in
    # order — the next unlocks only after persona_unlock_step_samples more clean
    # closes accrue; account distress (drawdown / kill switch) refreezes ALL. Default
    # OFF → the static operator_decidable dict applies (P1-P3 behavior, zero change).
    persona_progressive_unlock_enabled: bool = False
    persona_unlock_step_samples: int = 25
    # --- Pyramid add-on (P4, roll persona): add to winners, bounded ---
    # DEFAULT OFF. When on, a winning position may be scaled into up to the persona's
    # max_adds, each add = add_fraction of the base notional, HARD-CAPPED by the risk
    # engine's total/per-symbol exposure limits. add_trigger_pct = unrealized profit %
    # required before the next add. NOTE: this flag governs the pyramid DECISION; live
    # scale-in order placement is a separate, owner-gated step (it places orders).
    persona_pyramid_enabled: bool = False
    persona_pyramid_add_trigger_pct: float = 1.0
    # Durable, cross-session rollout auto-advance (P4 ops). When True, the
    # `persona_rollout` scheduler job (every 8h) advances the ladder one health-gated
    # step at a time (progressive_unlock → learning). Default OFF. NEVER auto-advances
    # owner-gated items (demo leverage cap, roll, pyramid live). See persona_rollout.py.
    persona_auto_advance_enabled: bool = False


class UpdateConfig(BaseModel):
    # On-demand self-upgrade (git pull). Human-triggered only; never a daemon.
    enabled: bool = True
    branch: str = "master"
    last_check_time: str | None = None
    last_known_remote_commit: str | None = None
    current_commit: str | None = None


class UniverseConfig(BaseModel):
    enabled: bool = False
    refresh_interval_min: int = 10
    market_type: str = "crypto"
    max_candidates: int = 30
    core_pool_size: int = 10
    min_volume_24h_usdt: float = 5_000_000
    max_spread_bps: float = 25.0
    min_depth_10bps_usd: float = 25_000
    explore_ratio: float = 0.2
    # Static family quota — ACTIVE whenever regime detection returns no state
    # (~75% of refreshes in practice, so this is the dominant allocation path, not
    # a rare fallback). Rebalanced 2026-07-03 from the data: the old default gave
    # derivatives 0.35 + event 0.05 = 40% of the quota to two families that
    # produce ZERO / ~1% of candidates (funding/OI feed gap), so their share went
    # unused and momentum backfilled it → 84% momentum in the live pool. New split
    # reflects which families actually fire: momentum trimmed, volatility &
    # short_bias given explicit shares (they previously relied on the 0.3 default
    # cap), derivatives/event kept small for when their feeds return. Sums to 1.0.
    family_quota: dict[str, float] = Field(default_factory=lambda: {
        "momentum": 0.30, "mean_reversion": 0.25, "volatility": 0.20,
        "short_bias": 0.10, "derivatives": 0.10, "event": 0.05,
    })
    # Per-regime family quota overrides (2A+.9). Maps regime state
    # (bull/bear/sideways/volatile) → family quota dict. When the current regime
    # has an entry here, it REPLACES `family_quota` for that refresh; otherwise
    # allocation falls back to the static `family_quota` above. Rationale: a bear
    # regime should give short_bias/derivatives more of the universe and starve
    # long-only momentum, a volatile regime should favour the volatility family,
    # etc. Empty dict → fully backward compatible (static quota everywhere).
    family_quota_by_regime: dict[str, dict[str, float]] = Field(default_factory=lambda: {
        "bull": {"momentum": 0.40, "mean_reversion": 0.15, "derivatives": 0.25,
                 "volatility": 0.10, "event": 0.05, "short_bias": 0.05},
        "bear": {"short_bias": 0.30, "derivatives": 0.30, "mean_reversion": 0.20,
                 "momentum": 0.10, "volatility": 0.05, "event": 0.05},
        "sideways": {"mean_reversion": 0.35, "momentum": 0.20, "derivatives": 0.25,
                     "volatility": 0.10, "event": 0.05, "short_bias": 0.05},
        "volatile": {"volatility": 0.30, "derivatives": 0.25, "momentum": 0.20,
                     "mean_reversion": 0.15, "event": 0.05, "short_bias": 0.05},
    })
    blacklist: list[str] = Field(default_factory=list)
    fallback_symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    snapshot_ttl_enabled: bool = True
    snapshot_ttl_hours: float = 24.0
    # Per-family scoring weight multipliers (operator-adjustable via adjust_scanner_weight).
    # Default 1.0 means no bias; values >1 boost that family's candidates, <1 suppresses.
    scanner_weights: dict[str, float] = Field(default_factory=dict)
    # Scanner-family regime scoring (P1b 2026-07-02, Position B). The regime→direction
    # and regime→family score deltas the ranker applies per candidate. This is a SOFT,
    # scanner-family-level signal that applies to ALL opportunities — including the ~70%
    # that have NO linked strategy (the orphan gap) — NOT a hard block. It deliberately
    # stays soft per owner directive (high-dimensional, refutable self-evolution; no
    # low-dimensional symbol/direction/scanner hard interception; see memory
    # negfeedback-high-dimensional-evolve). Defaults reproduce the prior hardcoded ranker
    # values EXACTLY (verified no-op) — extracted to config so the regime affinity lives
    # at the scanner-family layer, is operator/learning-tunable, and is documented rather
    # than buried in _compute_trade_score. Locked by spec SP-S06/SP-S07.
    scanner_regime_scoring: dict[str, float] = Field(default_factory=lambda: {
        "bear_short": 20.0, "bear_long": -25.0,
        "bull_long": 20.0, "bull_short": -20.0,
        "volatile_penalty": 10.0,
        "sideways_trend_penalty": 25.0, "sideways_reversion_bonus": 15.0,
    })


class RegimeConfig(BaseModel):
    enabled: bool = True
    min_hold_periods: int = 3
    bias_threshold: float = 0.30
    short_enabled: bool = True


class HistoricalDataConfig(BaseModel):
    """Local OHLCV storage and bulk download settings."""
    enabled: bool = True
    max_bars_per_pair: int = 50_000
    default_months: int = 6
    nightly_backfill_enabled: bool = True
    nightly_backfill_hour_cst: int = 3
    min_bars_for_backtest: int = 200


class EventTriggerConfig(BaseModel):
    """Real-time-ish event detection on price_feed ticks."""
    enabled: bool = True
    price_move_pct_threshold: float = 3.0
    volume_surge_multiplier: float = 3.0
    watch_universe_members: bool = True
    trigger_strategy_design: bool = True
    strategy_design_min_move_pct: float = 5.0


class StrategyFactoryConfig(BaseModel):
    """Controls strategy generation velocity and validation gates."""
    backtest_gate_enabled: bool = True
    sweep_enabled: bool = True
    sweep_max_variants: int = 100
    sweep_use_optuna: bool = False
    sweep_n_trials: int = 60
    sweep_optuna_timeout_sec: float = 120.0
    sweep_top_k: int = 10
    event_driven_enabled: bool = True
    event_cooldown_minutes: int = 30
    event_min_score: float = 60.0
    walk_forward_enabled: bool = True
    walk_forward_folds: int = 3
    walk_forward_min_bars: int = 150
    high_trade_count_threshold: int = 30
    min_runtime_days_relaxed: int = 3


class GuardConfig(BaseModel):
    enabled: bool = True
    enforce_in_paper_ranker: bool = True
    auto_promote_enabled: bool = True
    min_matching_trades: int = 3
    max_score_penalty: float = 40.0


class TunnelConfig(BaseModel):
    enabled: bool = False
    tunnel_id: str = ""
    hostname: str = ""
    credentials_path: str = ""
    cloudflared_path: str = ""
    metrics_port: int = 0


class NetworkConfig(BaseModel):
    # Structured proxy definition (source of truth). ``proxy_url`` below is
    # recomposed from these three parts by the model validator; every consumer
    # (aletheia.net) still reads ``proxy_url``. Default 7890 (Clash/Mihomo common
    # port). Set proxy_host="" to disable (direct). On machines without a proxy the
    # reachability check in net.py falls back to direct automatically (unless
    # ``enforce`` is on) — so this default is safe on all machines.
    proxy_protocol: Literal["http", "https", "socks5"] = "http"
    proxy_host: str = "127.0.0.1"
    proxy_port: int = 7890
    # Composed URL, kept for backward compatibility with existing consumers and
    # legacy config files. Recomputed from the parts on every load/save.
    proxy_url: str = "http://127.0.0.1:7890"
    no_proxy: str = ""    # comma-separated bypass list, e.g. "127.0.0.1,localhost"
    # Fail-closed policy. When True, outbound traffic MUST go through the proxy: if
    # the proxy is unreachable the request fails rather than silently connecting
    # direct (which would leak the real IP to exchanges / providers). When False,
    # an unreachable proxy falls back to a direct connection (availability first).
    # Default False for portability — a fresh install / CI box without a proxy must
    # not be bricked by a strict default. Enable it in Settings for production.
    enforce: bool = False

    @model_validator(mode="after")
    def _sync_proxy_url(self) -> "NetworkConfig":
        fields_set = self.model_fields_set
        structured_set = bool(
            {"proxy_protocol", "proxy_host", "proxy_port"} & fields_set
        )
        # Legacy migration: a config predating the structured fields carries only
        # proxy_url. Parse it back into the parts so the UI reflects reality.
        if "proxy_url" in fields_set and not structured_set and self.proxy_url:
            self.parse_proxy_url()
        # Recompose the canonical URL from the parts (empty host = disabled/direct).
        self.recompose_proxy_url()
        return self

    def recompose_proxy_url(self) -> None:
        """Rebuild proxy_url from protocol/host/port. Empty host = disabled (direct).

        Shared by the model validator and the /settings/network route (setattr does
        not re-run the validator, so the route calls this explicitly after mutating).
        """
        host = (self.proxy_host or "").strip()
        self.proxy_url = f"{self.proxy_protocol}://{host}:{self.proxy_port}" if host else ""

    def parse_proxy_url(self) -> None:
        """Decompose an externally-set proxy_url back into protocol/host/port."""
        from urllib.parse import urlparse

        parsed = urlparse(self.proxy_url)
        scheme = (parsed.scheme or "http").lower()
        if scheme in ("socks", "socks5h"):
            scheme = "socks5"
        self.proxy_protocol = scheme if scheme in ("http", "https", "socks5") else "http"
        if parsed.hostname:
            self.proxy_host = parsed.hostname
        if parsed.port:
            self.proxy_port = parsed.port


class PiDaemonConfig(BaseModel):
    enabled: bool = True
    debounce_sec: float = 2.0      # collect burst signals before evaluating
    cooldown_sec: float = 45.0     # minimum gap between evaluation cycles
    heartbeat_sec: float = 60.0    # fallback polling interval (safety net)


class NewsFeedEntry(BaseModel):
    name: str
    url: str
    source: str
    categories: list[str] = Field(default_factory=list)
    enabled: bool = True


_DEFAULT_NEWS_FEEDS: list[dict] = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "source": "coindesk", "categories": ["crypto"], "enabled": True},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss", "source": "cointelegraph", "categories": ["crypto"], "enabled": True},
    {"name": "Federal Reserve Press", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "source": "fed", "categories": ["macro"], "enabled": True},
    {"name": "SEC Press Releases", "url": "https://www.sec.gov/news/pressreleases.rss", "source": "sec-press", "categories": ["regulatory", "official"], "enabled": True},
    {"name": "CFTC Press Releases", "url": "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "source": "cftc-press", "categories": ["regulatory", "official"], "enabled": True},
    {"name": "Ethereum Foundation Blog", "url": "https://blog.ethereum.org/feed.xml", "source": "ethereum-foundation", "categories": ["crypto", "protocol", "official"], "enabled": True},
    {"name": "MarketWatch Top Stories", "url": "http://feeds.marketwatch.com/marketwatch/topstories/", "source": "marketwatch", "categories": ["markets"], "enabled": True},
    {"name": "CNBC Economy", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", "source": "cnbc-economy", "categories": ["macro"], "enabled": True},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml", "source": "theblock", "categories": ["crypto"], "enabled": False},
    {"name": "Decrypt", "url": "https://decrypt.co/feed", "source": "decrypt", "categories": ["crypto"], "enabled": False},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed", "source": "bitcoinmagazine", "categories": ["crypto"], "enabled": False},
    {"name": "Nikkei Asia", "url": "https://asia.nikkei.com/rss/feed/nar", "source": "nikkei-asia", "categories": ["markets", "asia"], "enabled": False},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "source": "yahoo-finance", "categories": ["markets"], "enabled": False},
]


class NewsConfig(BaseModel):
    enabled: bool = True
    fetch_interval_minutes: int = 15
    retention_days: int = 7
    feeds: list[NewsFeedEntry] = Field(default_factory=lambda: [NewsFeedEntry(**f) for f in _DEFAULT_NEWS_FEEDS])


class SerenityConfig(BaseModel):
    """Serenity Research school — long-horizon industrial-chain analyst lens.

    Phase 3 (Option A, D5): promote theme → enqueue signal_pending PiDecisions
    (PI remains sole trade-decision maker). auto_promote is default OFF;
    manual-only promotion preserves the human-curator workflow.
    """
    auto_promote: bool = False
    min_score_for_pi: int = 72
    # Phase 4 — long-horizon holding parameters. Wider than the short-term defaults
    # because Serenity holdings are industrial-chain bets measured in weeks/months,
    # not intraday swings.
    stop_loss_pct: float = 8.0
    max_hold_days: int = 90


class AletheiaConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    operator: OperatorConfig = Field(default_factory=OperatorConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    strategy_factory: StrategyFactoryConfig = Field(default_factory=StrategyFactoryConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    tunnel: TunnelConfig = Field(default_factory=TunnelConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    historical_data: HistoricalDataConfig = Field(default_factory=HistoricalDataConfig)
    event_triggers: EventTriggerConfig = Field(default_factory=EventTriggerConfig)
    pi_daemon: PiDaemonConfig = Field(default_factory=PiDaemonConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    serenity: SerenityConfig = Field(default_factory=SerenityConfig)
