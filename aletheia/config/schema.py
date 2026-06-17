"""Config schema using Pydantic v2 models."""

import logging
from pydantic import BaseModel, Field, field_validator

_logger = logging.getLogger("aletheia.config")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class AppConfig(BaseModel):
    name: str = "Aletheia Trading OS"
    dashboard_port: int = 8787
    auto_open_browser: bool = True
    bind_host: str = "127.0.0.1"

    @field_validator("bind_host")
    @classmethod
    def validate_bind_host(cls, v: str) -> str:
        if v not in _LOOPBACK_HOSTS:
            _logger.warning("bind_host=%s is not loopback — API will be network-exposed without authentication", v)
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
    # Symbol list — semantics determined by risk.symbol_filter_mode:
    #   "whitelist" → only these may trade; "blacklist" → these are blocked.
    symbol_list: list[str] = Field(default_factory=list)
    # Where autonomous paper trades execute:
    #   "bitget_demo"   → real Bitget demo/simulated-trading API (paptrading header,
    #                     USDT-futures only; exchange-side record, higher fidelity).
    #   "paper_internal"→ local simulated fills (no exchange call). Fallback when the
    #                     demo endpoint is unavailable. Requires mode == "demo".
    paper_execution_mode: str = "bitget_demo"


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
    # US equities to observe (Yahoo Finance tickers). stock_observer runs only
    # during US market hours (Mon–Fri 14:30–21:00 UTC). Empty = disabled.
    observed_stocks: list[str] = Field(default_factory=list)
    # Polymarket category slugs (or keywords) to observe. prediction_observer
    # fetches active markets matching these categories every 30 minutes.
    observed_prediction_markets: list[str] = Field(default_factory=lambda: ["crypto", "politics"])
    # US equities to observe — canonical Yahoo Finance tickers (e.g. "AAPL").
    # equity_observer fetches daily bars via yfinance AND real-time price via
    # Bitget xStocks (if a matching ON-series token exists). Runs Mon–Fri
    # 14:30–21:00 UTC (US market hours). Empty = disabled.
    observed_equities: list[str] = Field(default_factory=list)
    # Deprecated: use observed_equities instead. Kept for backwards compatibility
    # so existing config.yaml files continue to load without error.
    observed_stocks: list[str] = Field(default_factory=list)
    # Bitget tokenized xStocks (ON-series spot symbols). market_observer observes
    # these via Bitget CCXT (no API key needed); asset_class="equity", never traded.
    observed_xstocks: list[str] = Field(
        default_factory=lambda: ["AAPLON/USDT", "TSLAON/USDT", "NVDAON/USDT", "SPYON/USDT"]
    )


class LiveConfig(BaseModel):
    state: str = "live_disabled"
    first_live_confirmation_done: bool = False


class AgentConfig(BaseModel):
    permission_mode: str = "Observe"
    autonomous_learning: bool = True


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
    max_demo_single_trade_risk_pct: float = 2.0
    max_futures_leverage_live: int = 2
    max_futures_leverage_demo: int = 5
    max_open_positions_live: int = 3
    max_open_positions_demo: int = 6
    # "whitelist" = only symbol_list may trade; "blacklist" = all except symbol_list.
    symbol_filter_mode: str = "blacklist"
    symbol_list: list[str] = Field(default_factory=list)
    allowed_exchange: str = "bitget"
    allow_short_demo: bool = True
    allow_short_live: bool = True
    require_risk_check_for_all_orders: bool = True


class TelegramConfig(BaseModel):
    enabled: bool = False
    allowed_chat_ids: list[int] = Field(default_factory=list)


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


class TradingConfig(BaseModel):
    # Paper position exit rules used by the position_manager. Non-zero defaults so
    # the autonomous loop actually closes positions and realizes PnL.
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 4.0
    max_position_hold_hours: float = 72.0
    # Edge Lab: realistic paper fill simulation
    slippage_bps: float = 5.0        # basis points of adverse slippage on paper fills
    execution_fee_pct: float = 0.1   # % fee per side (replaces hardcoded 0.1%)
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
    family_quota: dict[str, float] = Field(default_factory=lambda: {
        "momentum": 0.35, "mean_reversion": 0.25,
        "derivatives": 0.35, "event": 0.05,
    })
    blacklist: list[str] = Field(default_factory=list)
    fallback_symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    snapshot_ttl_enabled: bool = True
    snapshot_ttl_hours: float = 24.0


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
    # Default to 7890 (Clash/Mihomo common port). Set to "" to disable.
    # On machines without a proxy the reachability check in net.py falls back to
    # direct connection automatically — so this default is safe on all machines.
    proxy_url: str = "http://127.0.0.1:7890"
    no_proxy: str = ""    # comma-separated bypass list, e.g. "127.0.0.1,localhost"


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
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    tunnel: TunnelConfig = Field(default_factory=TunnelConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
