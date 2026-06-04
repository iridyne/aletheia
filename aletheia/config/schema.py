"""Config schema using Pydantic v2 models."""

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    name: str = "Aletheia Trading OS"
    dashboard_port: int = 8787
    auto_open_browser: bool = True
    bind_host: str = "127.0.0.1"


class RuntimeConfig(BaseModel):
    gateway_mode: str = "background"
    scheduler_enabled: bool = True
    paused: bool = False
    # Global trading brake. When true, the risk engine rejects every proposal
    # (paper and live). Set by human action or auto-tripped on daily-loss limit.
    kill_switch_active: bool = False
    # Optional Git versioning of data/knowledge, data/playbooks, data/reports.
    git_enabled: bool = False


class ExchangeConfig(BaseModel):
    name: str = "bitget"
    mode: str = "demo"
    enabled_markets: dict[str, bool] = Field(default_factory=lambda: {"spot": True, "usdt_futures": True})
    tradable_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])


class LiveConfig(BaseModel):
    state: str = "live_disabled"
    first_live_confirmation_done: bool = False


class AgentConfig(BaseModel):
    permission_mode: str = "Research"
    autonomous_learning: bool = True
    autonomous_paper_trading: bool = False


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
    live: LiveConfig = Field(default_factory=LiveConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
