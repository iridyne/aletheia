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


class AletheiaConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
