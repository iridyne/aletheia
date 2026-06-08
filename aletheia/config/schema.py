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


class ExchangeConfig(BaseModel):
    name: str = "bitget"
    mode: str = "demo"
    enabled_markets: dict[str, bool] = Field(default_factory=lambda: {"spot": True, "usdt_futures": True})
    # Trading whitelist only — which symbols may be traded via this exchange.
    # Market data observation is controlled by MarketDataConfig.observed_symbols.
    tradable_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])


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
    # during US market hours (Mon–Fri 14:30–21:00 UTC).
    observed_stocks: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "NVDA", "AAPL"])
    # Polymarket category slugs (or keywords) to observe. prediction_observer
    # fetches active markets matching these categories every 30 minutes.
    observed_prediction_markets: list[str] = Field(default_factory=lambda: ["crypto", "politics"])
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


class ProviderConfig(BaseModel):
    base_url: str = ""
    protocol: str = "chat_completions"
    model: str = "default"
    enabled: bool = True
    command: str = ""
    mode: str = ""
    # Name of the secret holding this provider's API key (the key itself lives
    # in keyring / secrets.enc, never in config.yaml). e.g. "model.openai_compatible.api_key"
    api_key_secret_ref: str | None = None
    # Selected Codex model slug (codex_cli provider only). None = codex default.
    # No secret_ref: codex self-manages its OAuth token under ~/.codex.
    codex_model: str | None = None


class ModelsConfig(BaseModel):
    default_provider: str = "openai_compatible"
    providers: dict[str, ProviderConfig] = Field(default_factory=lambda: {
        "openai_compatible": ProviderConfig(
            base_url="http://localhost:8000/v1",
            protocol="chat_completions",
            model="default",
        ),
        "anthropic_compatible": ProviderConfig(
            base_url="http://localhost:8001",
            model="default",
        ),
        "codex_cli": ProviderConfig(
            enabled=False,
            command="codex",
            mode="exec",
        ),
    })


class RiskConfig(BaseModel):
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
    allowed_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
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
    max_agent_failure_ratio: float = 0.5
    llm_unavailable_alert_after: int = 3
    # Silent-degradation guard: flag the market-data feed as stale when the newest
    # snapshot is older than this (market data is collected every 5 min).
    market_data_max_staleness_min: int = 30
    # Gateway supervisor (process self-healing).
    supervisor_max_restarts: int = 5
    supervisor_restart_window_sec: int = 600
    supervisor_backoff_cap_sec: int = 60


class RetentionConfig(BaseModel):
    # Telemetry pruning (days). Trust-bearing records are NEVER pruned.
    agent_runs_days: int = 30
    tool_calls_days: int = 30
    run_steps_days: int = 30
    market_snapshots_days: int = 30
    portfolio_snapshots_days: int = 90
    jsonl_max_mb: float = 50.0


class TradingConfig(BaseModel):
    # Paper position exit rules used by the position_manager. Non-zero defaults so
    # the autonomous loop actually closes positions and realizes PnL.
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 4.0
    max_position_hold_hours: float = 72.0
    # Minimum LLM confidence for paper_trader to open a position
    # (high | medium | speculative). Below this, the opportunity is recorded but
    # not traded.
    min_confidence_to_trade: str = "medium"


class UpdateConfig(BaseModel):
    # On-demand self-upgrade (git pull). Human-triggered only; never a daemon.
    enabled: bool = True
    branch: str = "master"
    last_check_time: str | None = None
    last_known_remote_commit: str | None = None
    current_commit: str | None = None


class AletheiaConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
