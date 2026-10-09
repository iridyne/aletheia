"""Settings API routes."""

import logging

import asyncio
import json
from typing import Any, Literal, Optional, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from typing_extensions import TypedDict

from aletheia.config.loader import edit_config, load_config, mutate_config
from aletheia.config.execution_costs import (
    EXECUTION_FEE_PCT_BOUNDS,
    FALLBACK_SPREAD_BPS_BOUNDS,
    SLIPPAGE_BPS_BOUNDS,
)
from aletheia.config.schema import (
    ModelSelection,
    AgentConfig,
    AppConfig,
    ControllerConfig,
    EconomicsConfig,
    EventTriggerConfig,
    ExchangeConfig,
    FastTriggerConfig,
    GuardConfig,
    HealthConfig,
    HistoricalDataConfig,
    LearningConfig,
    LiveConfig,
    MarketDataConfig,
    DerivativesComparisonVenue,
    ModeRiskPolicy,
    NetworkConfig,
    NewsConfig,
    ObservationConfig,
    OperatorConfig,
    PiDaemonConfig,
    RegimeConfig,
    RemoteControlConfig,
    RetentionConfig,
    RiskConfig,
    RuntimeConfig,
    SerenityConfig,
    NotificationHour,
    TelegramConfig,
    TradingConfig,
    UniverseConfig,
    UpdateConfig,
    WebhookConfig,
    WebhookTimeoutSeconds,
    default_mode_risk_policy,
    validate_mode_risk_policy,
)
from aletheia.risk.policy import risk_policy_hash

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Worker/agent permission surface (cfg.agent.permission_mode). Distinct from the
# Operator surface (cfg.operator.permission_mode: ReadOnly/Research/PaperAuto/
# AllowEditing/Auto) — this endpoint correctly governs only the worker side, so
# the 3-value allowlist is intentional, not a bug (CR-AR-02 was a false positive).
from aletheia.agent_shell.permissions import VALID_WORKER_MODES as VALID_PERMISSION_MODES


def _mutate_config_with_candidate_publication_permit(mutator):
    """Serialize frozen Candidate identity writes with snapshot publication."""

    from aletheia.storage import repositories as repo

    with repo.transaction(immediate=True):
        return mutate_config(mutator)




def _reject_live_identity_change(cfg, *, changed: bool) -> None:
    """Keep an unlocked account bound to the exact selected configuration."""
    if changed and getattr(cfg.live, "state", "") == "live_limited":
        raise HTTPException(
            409,
            detail={
                "error": "live_unlock_identity_conflict",
                "message": "Lock live trading and re-run the selected-account preflight before changing its identity.",
            },
        )


# --- Inline-secret redaction ---------------------------------------------------
# Secrets must never leave the backend via a GET response, an echoed POST body,
# or an audit record. `config.yaml` can hold a credential-bearing proxy URL.
def _redact_proxy_url(url):
    """Strip embedded credentials from a proxy URL: scheme://user:pass@host -> scheme://***@host."""
    if not url:
        return url
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(str(url))
        if parts.username or parts.password:
            host = parts.hostname or ""
            if parts.port:
                host = f"{host}:{parts.port}"
            return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    except Exception:  # noqa: BLE001 — never let redaction raise into a response
        return "***"
    return url


def _redacted_network(net: dict) -> dict:
    net = dict(net)
    if net.get("proxy_url"):
        net["proxy_url"] = _redact_proxy_url(net["proxy_url"])
    return net


def _redacted_webhook(wh: dict) -> dict:
    wh = dict(wh)
    secret = wh.pop("secret", None)
    wh["secret_configured"] = bool(secret)
    return wh


class SettingsRequest(BaseModel):
    """Strict request body for Settings mutations.

    The dashboard should fail loudly when a control sends a stale or misspelled
    config key. Pydantic's default `extra=ignore` would otherwise make a UI/API
    mismatch look like a successful save while persisting nothing.
    """

    model_config = ConfigDict(extra="forbid")


def _validated_settings_section(section_type, before: dict[str, Any], updates: dict[str, Any], *, name: str):
    """Validate the complete resulting config section before persistence."""
    try:
        return section_type.model_validate({**before, **updates})
    except ValidationError as exc:
        fields = sorted(
            {
                ".".join(str(part) for part in error.get("loc", ()))
                for error in exc.errors()
            }
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_configuration",
                "message": f"{name} contains invalid values",
                "fields": fields,
            },
        ) from exc


class PermissionModeRequest(SettingsRequest):
    mode: str


class ExchangeModeRequest(SettingsRequest):
    mode: str


class TradingMandateRequest(SettingsRequest):
    mandate: dict[str, Any] | None


class SettingsProviderResponse(BaseModel):
    """The non-secret provider projection returned by ``GET /settings``."""

    type: str
    base_url: str
    protocol: str
    model: str
    enabled: bool
    command: str
    mode: str
    codex_model: str | None
    workspace_url: str | None
    auth_scheme: str
    auth_type: str
    oauth_expires_at: int | None
    model_fallback_enabled: bool
    fallback_models: list[str]
    inference_max_concurrency: int
    inference_requests_per_minute: int
    inference_tpm: int
    inference_queue_capacity: int
    inference_reserved_high_priority_slots: int
    inference_timeout_sec: float


class SettingsModelsResponse(BaseModel):
    default_provider: str
    deep: ModelSelection | None
    fast: ModelSelection | None
    providers: dict[str, SettingsProviderResponse]


class SettingsWebhookResponse(BaseModel):
    enabled: bool
    url: str
    secret_configured: bool
    notify_trade_fills: bool
    notify_alerts: bool
    notify_daily_summary: bool
    timeout_sec: float
    last_delivery: "SettingsWebhookDeliveryResponse | None"


class SettingsWebhookDeliveryResponse(BaseModel):
    status: Literal["delivered", "failed"]
    attempted_at: str
    receiver_host: str
    event_type: str
    entity_type: str | None
    entity_id: str | None
    http_status: int | None
    error_class: str | None
    latency_ms: int


class SettingsTunnelResponse(BaseModel):
    enabled: bool
    tunnel_id: str
    hostname: str
    cloudflared_path: str
    metrics_port: int


class SettingsPersonaRolloutResponse(BaseModel):
    """Durable rollout status; nested evidence is intentionally read-only."""

    model_config = ConfigDict(extra="allow")
    desired_progressive_unlock: bool
    desired_auto_advance: bool
    status: str | None
    actual_authorized_params: list[str]
    evidence: dict[str, Any]
    events: list[dict[str, Any]]
    rollout: dict[str, Any] | None = None


class SettingsTradingMandateResponse(BaseModel):
    """Sanitized, resolver-backed mandate view with forward-compatible fields."""

    model_config = ConfigDict(extra="allow")


class LearningPathOption(BaseModel):
    path_id: str
    label: str
    supported: bool
    status: Literal[
        "supported",
        "consumer_unwired",
        "measurement_unwired",
        "authority_unwired",
    ]


class PiDaemonRuntimeStatus(BaseModel):
    """Effective in-process state for the configured System Operator daemon."""

    running: bool
    event_set: bool
    lock_held: bool
    task_generation: int
    started_at: float | None
    active_params: dict[str, float] | None
    last_error_summary: str | None


class SettingsResponse(BaseModel):
    """Authoritative, redacted response contract for ``GET /settings``.

    The individual config sections are the same Pydantic models used by the
    loader.  The few projections that redact secrets or add UI-only metadata
    have dedicated models so OpenAPI (and generated frontend types) describe
    the actual wire shape rather than the raw config file.
    """

    app: AppConfig
    runtime: RuntimeConfig
    exchange: ExchangeConfig
    live: LiveConfig
    agent: AgentConfig
    operator: OperatorConfig
    models: SettingsModelsResponse
    risk: RiskConfig
    update: UpdateConfig
    trading: TradingConfig
    persona_rollout: SettingsPersonaRolloutResponse
    health: HealthConfig
    retention: RetentionConfig
    telegram: TelegramConfig
    webhook: SettingsWebhookResponse
    market_data: MarketDataConfig
    universe: UniverseConfig
    fast_trigger: FastTriggerConfig
    guard: GuardConfig
    tunnel: SettingsTunnelResponse
    remote_control: RemoteControlConfig
    regime: RegimeConfig
    network: NetworkConfig
    historical_data: HistoricalDataConfig
    event_triggers: EventTriggerConfig
    pi_daemon: PiDaemonConfig
    pi_daemon_runtime: PiDaemonRuntimeStatus
    news: NewsConfig
    serenity: SerenityConfig
    controller: ControllerConfig
    observation: ObservationConfig
    learning: LearningConfig
    economics: EconomicsConfig
    trading_mandate: SettingsTradingMandateResponse | None = None
    learning_path_options: list[LearningPathOption] | None


def _trading_mandate_view(cfg) -> dict[str, Any] | None:
    value = getattr(cfg, "trading_mandate", None)
    if value is None:
        return None
    from aletheia.trading.trading_mandate import resolve_trading_mandate

    try:
        resolution = resolve_trading_mandate(cfg)
    except Exception:
        resolution = None
    if resolution is not None and resolution.mandate is not None:
        return {
            "enabled": True,
            "resolution_status": "active",
            **resolution.mandate.model_dump(mode="json"),
        }

    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else {}
    view: dict[str, Any] = {
        "resolution_status": (
            resolution.status if resolution is not None else "mandate_unavailable"
        ),
    }
    for key in (
        "enabled",
        "contract_version",
        "mandate_id",
        "mandate_version",
        "status",
        "enforcement_mode",
    ):
        item = raw.get(key)
        if key in raw and (item is None or isinstance(item, (str, int, float, bool))):
            view[key] = item
    if resolution is not None and resolution.validation_errors:
        view["validation_errors"] = list(resolution.validation_errors)
    return view


@router.get("", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    cfg = load_config()
    from aletheia.agent_runtime.pi_daemon import daemon_status
    try:
        from extensions.learning.registry import paper_influence_path_options
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.registry"}:
            raise
        learning_path_options = None
    else:
        learning_path_options = list(paper_influence_path_options())
    from aletheia.trading.persona_rollout import persona_rollout_status
    from aletheia.notifications import webhook_delivery_status
    providers_safe = {}
    for name, p in cfg.models.providers.items():
        providers_safe[name] = {
            "type": p.type,
            "base_url": p.base_url,
            "protocol": p.protocol,
            "model": p.model,
            "enabled": p.enabled,
            "command": p.command,
            "mode": p.mode,
            "codex_model": p.codex_model,
            "workspace_url": p.workspace_url,
            "auth_scheme": p.auth_scheme,
            "auth_type": p.auth_type,
            "oauth_expires_at": p.oauth_expires_at,
            "model_fallback_enabled": p.model_fallback_enabled,
            "fallback_models": list(p.fallback_models or []),
            "inference_max_concurrency": p.inference_max_concurrency,
            "inference_requests_per_minute": p.inference_requests_per_minute,
            "inference_tpm": p.inference_tokens_per_minute,
            "inference_queue_capacity": p.inference_queue_capacity,
            "inference_reserved_high_priority_slots": p.inference_reserved_high_priority_slots,
            "inference_timeout_sec": p.inference_timeout_sec,
        }
    return {
        "app": cfg.app.model_dump(mode="json"),
        "runtime": cfg.runtime.model_dump(mode="json"),
        "exchange": cfg.exchange.model_dump(mode="json"),
        "live": cfg.live.model_dump(mode="json"),
        "agent": cfg.agent.model_dump(mode="json"),
        "operator": cfg.operator.model_dump(mode="json"),
        "models": {
            "default_provider": cfg.models.default_provider,
            "deep": cfg.models.deep,
            "fast": cfg.models.fast,
            "providers": providers_safe,
        },
        "risk": cfg.risk.model_dump(mode="json"),
        "update": cfg.update.model_dump(mode="json"),
        "trading": cfg.trading.model_dump(mode="json"),
        "persona_rollout": persona_rollout_status(cfg),
        "health": cfg.health.model_dump(mode="json"),
        "retention": cfg.retention.model_dump(mode="json"),
        "telegram": cfg.telegram.model_dump(mode="json"),
        "webhook": {
            **{k: v for k, v in cfg.webhook.model_dump(mode="json").items() if k != "secret"},
            "last_delivery": webhook_delivery_status(),
            "secret_configured": bool(cfg.webhook.secret),
        },
        "market_data": cfg.market_data.model_dump(mode="json"),
        "universe": cfg.universe.model_dump(mode="json"),
        "fast_trigger": cfg.fast_trigger.model_dump(mode="json"),
        "guard": cfg.guard.model_dump(mode="json"),
        "tunnel": {k: v for k, v in cfg.tunnel.model_dump(mode="json").items()
                   if k != "credentials_path"},
        "remote_control": cfg.remote_control.model_dump(mode="json"),
        "regime": cfg.regime.model_dump(mode="json"),
        "network": _redacted_network(cfg.network.model_dump(mode="json")),
        "historical_data": cfg.historical_data.model_dump(mode="json"),
        "event_triggers": cfg.event_triggers.model_dump(mode="json"),
        "pi_daemon": cfg.pi_daemon.model_dump(mode="json"),
        "pi_daemon_runtime": daemon_status(),
        "news": cfg.news.model_dump(mode="json"),
        "serenity": cfg.serenity.model_dump(mode="json"),
        "controller": cfg.controller.model_dump(mode="json"),
        "observation": cfg.observation.model_dump(mode="json"),
        "learning": cfg.learning.model_dump(mode="json"),
        "economics": cfg.economics.model_dump(mode="json"),
        # Read-only visibility keeps the config schema honest; unknown mandate
        # fields are intentionally omitted so this GET cannot echo credentials.
        "trading_mandate": _trading_mandate_view(cfg),
        "learning_path_options": learning_path_options,
    }


@router.post("/trading-mandate")
def set_trading_mandate(req: TradingMandateRequest):
    from aletheia.files.jsonl import write_audit
    from aletheia.trading.trading_mandate import TradingMandateConfig, resolve_trading_mandate

    candidate = TradingMandateConfig.model_validate(
        {"enabled": False} if req.mandate is None else req.mandate
    )
    resolution = resolve_trading_mandate(candidate)
    if resolution.status == "mandate_unavailable":
        raise HTTPException(
            422,
            detail={
                "code": "trading_mandate_invalid",
                "validation_errors": list(resolution.validation_errors),
            },
        )
    if resolution.status == "not_configured":
        candidate = TradingMandateConfig(enabled=False)

    before = None
    def mutator(cfg):
        nonlocal before
        before = _trading_mandate_view(cfg)
        cfg.trading_mandate = candidate
        return True

    mutate_config(mutator)
    cfg = load_config()
    after = _trading_mandate_view(cfg)

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "trading_mandate_changed",
        "entity_type": "trading_mandate",
        "before": before,
        "after": after,
        "message": "observe-only trading mandate updated",
    })
    return {"trading_mandate": after}


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------

class AutonomousLearningRequest(SettingsRequest):
    enabled: bool


@router.post("/autonomous-learning")
def set_autonomous_learning(req: AutonomousLearningRequest):
    with edit_config() as cfg:
        old = cfg.agent.autonomous_learning
        cfg.agent.autonomous_learning = req.enabled
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "autonomous_learning_changed",
                 "entity_type": "agent_config", "before": {"autonomous_learning": old},
                 "after": {"autonomous_learning": req.enabled},
                 "message": f"autonomous_learning {old} -> {req.enabled}"})
    return {"old": old, "new": req.enabled}


class BypassDistressGateRequest(SettingsRequest):
    enabled: bool


@router.post("/bypass-distress-gate")
def set_bypass_distress_gate(req: BypassDistressGateRequest):
    with edit_config() as cfg:
        old = cfg.agent.bypass_distress_gate
        cfg.agent.bypass_distress_gate = req.enabled
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "bypass_distress_gate_changed",
                 "entity_type": "agent_config", "before": {"bypass_distress_gate": old},
                 "after": {"bypass_distress_gate": req.enabled},
                 "message": f"bypass_distress_gate {old} -> {req.enabled}"})
    return {"old": old, "new": req.enabled}


# ---------------------------------------------------------------------------
# Exchange markets config
# ---------------------------------------------------------------------------

class ExchangeMarketsRequest(SettingsRequest):
    spot: bool
    usdt_futures: bool
    paper_execution_mode: Optional[str] = None
    confirm_exchange_divergence: bool = False


def _require_exchange_divergence_confirmation(alignment, confirmed: bool) -> None:
    if not alignment.diverged or confirmed:
        return
    raise HTTPException(
        409,
        detail={
            "code": "exchange_source_divergence_confirmation_required",
            "message": (
                "Market data and order execution would use different exchanges. "
                "Confirm this intentional divergence before saving."
            ),
            "execution_exchange": alignment.execution_exchange,
            "data_exchange": alignment.data_exchange,
        },
    )


@router.post("/exchange-markets")
def set_exchange_markets(req: ExchangeMarketsRequest):
    if not req.spot and not req.usdt_futures:
        raise HTTPException(400, "At least one market must be enabled")
    # Validate against the Risk vocabulary itself. The former hand-written set
    # accepted `binance_live` and `okx_live` — UTA credential profile names, not
    # execution modes — and the mutator below persists the value verbatim. Either
    # one makes resolve_risk_policy raise UnknownRiskExecutionMode, so every
    # subsequent proposal is rejected `unregistered_risk_execution_mode:...`:
    # a total trading halt reachable from one dashboard POST.
    from aletheia.risk.policy import DEMO_EXECUTION_MODES

    if req.paper_execution_mode is not None and req.paper_execution_mode not in DEMO_EXECUTION_MODES:
        allowed = ", ".join(sorted(DEMO_EXECUTION_MODES))
        raise HTTPException(400, f"paper_execution_mode must be one of: {allowed}")
    # Pre-validate outside the mutation lock to fail fast
    cfg_check = load_config()
    next_paper_mode = req.paper_execution_mode or cfg_check.exchange.paper_execution_mode
    _reject_live_identity_change(
        cfg_check,
        changed=(req.paper_execution_mode is not None and next_paper_mode != cfg_check.exchange.paper_execution_mode),
    )
    from aletheia.config.exchange_alignment import exchange_alignment_for_values
    alignment = exchange_alignment_for_values(
        paper_execution_mode=next_paper_mode,
        data_exchange=cfg_check.market_data.ccxt_exchange,
        configured_exchange=cfg_check.exchange.name,
    )
    if req.paper_execution_mode is not None and req.paper_execution_mode != cfg_check.exchange.paper_execution_mode:
        _require_exchange_divergence_confirmation(
            alignment, req.confirm_exchange_divergence,
        )

    old = None
    def mutator(cfg):
        nonlocal old
        old = cfg.exchange.model_dump(mode="json")
        cfg.exchange.enabled_markets = {"spot": req.spot, "usdt_futures": req.usdt_futures}
        if req.paper_execution_mode is not None:
            cfg.exchange.paper_execution_mode = req.paper_execution_mode
        return True

    _mutate_config_with_candidate_publication_permit(mutator)
    cfg = load_config()
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "exchange_markets_changed",
                 "entity_type": "exchange_config", "before": old,
                 "after": cfg.exchange.model_dump(mode="json"),
                 "message": "exchange config updated"})
    return {**cfg.exchange.model_dump(mode="json"), "exchange_alignment": alignment.to_dict()}


# ---------------------------------------------------------------------------
# Primary exchange unification
# ---------------------------------------------------------------------------

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP as _EXCHANGE_UNIFICATION_MAP
logger = logging.getLogger("aletheia.server.routes.settings")


class PrimaryExchangeRequest(SettingsRequest):
    exchange_id: str


@router.post("/primary-exchange")
def set_primary_exchange(req: PrimaryExchangeRequest):
    """Atomically set primary exchange across data, execution, and risk axes."""
    eid = req.exchange_id.strip().lower()
    valid = set(_EXCHANGE_UNIFICATION_MAP)
    if eid not in valid:
        raise HTTPException(400, f"exchange_id must be one of: {sorted(valid)}")

    # Pre-validate outside the mutation lock
    cfg_check = load_config()
    desired_paper_mode = _EXCHANGE_UNIFICATION_MAP[eid][1]
    _reject_live_identity_change(
        cfg_check,
        changed=(
            eid != str(cfg_check.exchange.name or "").lower()
            or desired_paper_mode != cfg_check.exchange.paper_execution_mode
        ),
    )

    old = None
    after = None
    def mutator(cfg):
        nonlocal old, after
        old = {
            "exchange.name": cfg.exchange.name,
            "exchange.paper_execution_mode": cfg.exchange.paper_execution_mode,
            "market_data.ccxt_exchange": cfg.market_data.ccxt_exchange,
            "risk.allowed_exchange": cfg.risk.allowed_exchange,
        }

        ccxt_ex, paper_mode = _EXCHANGE_UNIFICATION_MAP[eid]
        cfg.exchange.name = eid
        cfg.market_data.ccxt_exchange = ccxt_ex
        cfg.market_data.ccxt_fallback = ccxt_ex
        cfg.exchange.paper_execution_mode = paper_mode
        cfg.risk.allowed_exchange = eid

        after = {
            "exchange.name": cfg.exchange.name,
            "exchange.paper_execution_mode": cfg.exchange.paper_execution_mode,
            "market_data.ccxt_exchange": cfg.market_data.ccxt_exchange,
            "risk.allowed_exchange": cfg.risk.allowed_exchange,
        }
        return True

    _mutate_config_with_candidate_publication_permit(mutator)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "primary_exchange_changed",
        "entity_type": "exchange_config",
        "before": old, "after": after,
        "message": f"Primary exchange set to {eid}",
    })
    return {"exchange_id": eid, **after}


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------

class RuntimeConfigUpdate(SettingsRequest):
    scheduler_enabled: Optional[bool] = None
    git_enabled: Optional[bool] = None
    auto_reload: Optional[bool] = None


class EconomicsConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    budgets_shadow_enabled: Optional[bool] = None
    prompt_guardrail_detection_enabled: Optional[bool] = None


@router.post("/economics")
def update_economics_config(req: EconomicsConfigUpdate):
    """Persist the default-off Phase 2 shadow-observation controls."""
    with edit_config() as cfg:
        old = cfg.economics.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(cfg.economics, field, value)
        if not cfg.economics.enabled:
            cfg.economics.budgets_shadow_enabled = False
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "economics_config_changed", "entity_type": "economics_config",
        "before": old, "after": cfg.economics.model_dump(mode="json"),
        "message": f"Economics config updated: {list(updates.keys())}",
    })
    return {"updated": updates, "economics": cfg.economics.model_dump(mode="json")}


@router.post("/runtime")
def update_runtime_config(req: RuntimeConfigUpdate):
    with edit_config() as cfg:
        old = cfg.runtime.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(cfg.runtime, field, value)
    # Notify APScheduler if scheduler_enabled changed. The public resume entry
    # owns the durable pause-owner check, so Settings cannot bypass a gate lease.
    if "scheduler_enabled" in updates:
        try:
            from aletheia.agent_runtime.scheduler import pause_scheduler, resume_scheduler
            if cfg.runtime.scheduler_enabled:
                resume_scheduler()
            else:
                pause_scheduler()
        except Exception:
            logger.warning("settings: scheduler pause/resume failed after a runtime-config change", exc_info=True)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "runtime_config_changed",
                 "entity_type": "runtime_config", "before": old,
                 "after": cfg.runtime.model_dump(mode="json"),
                 "message": f"Runtime config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

_VALID_BIND_HOSTS = {"0.0.0.0", "::", "127.0.0.1", "localhost", "::1"}


class AppConfigUpdate(SettingsRequest):
    auto_open_browser: Optional[bool] = None
    equity_curve_mode: Optional[str] = None
    bind_host: Optional[str] = None

    @field_validator("equity_curve_mode")
    @classmethod
    def _validate_equity_curve_mode(cls, v):
        if v is not None and v not in ("full_cumulative", "window_relative"):
            raise ValueError("equity_curve_mode must be 'full_cumulative' or 'window_relative'")
        return v

    @field_validator("bind_host")
    @classmethod
    def _validate_bind_host(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("bind_host cannot be empty")
        if v not in _VALID_BIND_HOSTS:
            import re
            _ipv4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
            if not _ipv4.match(v):
                raise ValueError(
                    f"bind_host must be one of {sorted(_VALID_BIND_HOSTS)} or a valid IPv4 address"
                )
        return v


@router.post("/app")
def update_app_config(req: AppConfigUpdate):
    with edit_config() as cfg:
        old = cfg.app.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        if "bind_host" in updates:
            raise HTTPException(
                400,
                "bind_host is managed by Remote Control network-adapter selection",
            )
        for field, value in updates.items():
            setattr(cfg.app, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "app_config_changed",
                 "entity_type": "app_config", "before": old,
                 "after": cfg.app.model_dump(mode="json"),
                 "message": f"App config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# PI Daemon config
# ---------------------------------------------------------------------------

class FastTriggerConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    mode: Optional[Literal["off", "shadow", "execute"]] = None
    max_setups: Optional[int] = None
    authorization_ttl_minutes: Optional[int] = None


@router.post("/fast-trigger")
async def update_fast_trigger_config(req: FastTriggerConfigUpdate):
    """Persist bounded trigger authority and apply its watcher state immediately."""

    updates = req.model_dump(exclude_none=True)
    persisted: dict[str, Any] = {}

    def apply_update(cfg) -> bool:
        old = cfg.fast_trigger.model_dump(mode="json")
        cfg.fast_trigger = _validated_settings_section(
            FastTriggerConfig,
            old,
            updates,
            name="FastTriggerConfig",
        )
        persisted.update({"cfg": cfg, "old": old})
        return bool(updates)

    _mutate_config_with_candidate_publication_permit(apply_update)
    cfg = persisted["cfg"]
    old = persisted["old"]
    from aletheia.files.jsonl import write_audit

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "fast_trigger_config_changed",
        "entity_type": "fast_trigger_config",
        "before": old,
        "after": cfg.fast_trigger.model_dump(mode="json"),
        "message": f"Fast Trigger config updated: {list(updates.keys())}",
    })
    try:
        from aletheia.agent_runtime.fast_trigger_watch import (
            reconfigure_fast_trigger_watch,
        )

        runtime_status = await reconfigure_fast_trigger_watch(
            cfg, asyncio.get_running_loop()
        )
    except Exception as exc:
        runtime_status = {
            "running": False,
            "reason": f"fast_trigger_reconfigure_error:{type(exc).__name__}",
            "configured_mode": cfg.fast_trigger.mode,
        }
    return {"updated": updates, "runtime_status": runtime_status}

class PiDaemonConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    debounce_sec: Optional[float] = None
    cooldown_sec: Optional[float] = None
    heartbeat_sec: Optional[float] = None


@router.post("/pi_daemon")
async def update_pi_daemon_config(req: PiDaemonConfigUpdate):
    """Update System Operator daemon configuration.

    #97 note: the path `/pi_daemon`, the audit event `pi_daemon_config_changed`,
    and the entity type `pi_daemon_config` are LEGACY names preserved for
    backwards-compat with existing configs and audit trails. The subsystem is
    the System Operator daemon (see PiDaemonConfig docstring for the full
    naming registry). Any consumer parsing audit events must accept the legacy
    keys; new UI copy uses "System Operator daemon".
    """
    from aletheia.config.schema import PiDaemonConfig
    with edit_config() as cfg:
        old = cfg.pi_daemon.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        cfg.pi_daemon = _validated_settings_section(PiDaemonConfig, old, updates, name="PiDaemonConfig")
    from aletheia.agent_runtime.pi_daemon import daemon_status

    blocker: str | None = None
    if updates:  # every field can affect the active task
        try:
            from aletheia.agent_runtime.pi_daemon import reconfigure_pi_daemon
            loop = asyncio.get_event_loop()
            transition = await reconfigure_pi_daemon(
                enabled=cfg.pi_daemon.enabled,
                debounce_sec=cfg.pi_daemon.debounce_sec,
                cooldown_sec=cfg.pi_daemon.cooldown_sec,
                heartbeat_sec=cfg.pi_daemon.heartbeat_sec,
                loop=loop,
            )
            blocker = transition.get("blocker")
        except Exception:
            blocker = "reconfigure_error"

    runtime_status = {
        "configured": cfg.pi_daemon.enabled,
        **daemon_status(),
        "blocker": blocker,
    }
    expected_params = {
        "debounce_sec": cfg.pi_daemon.debounce_sec,
        "cooldown_sec": cfg.pi_daemon.cooldown_sec,
        "heartbeat_sec": cfg.pi_daemon.heartbeat_sec,
    }
    applied = (
        blocker is None
        and runtime_status["running"] is cfg.pi_daemon.enabled
        and (not cfg.pi_daemon.enabled or runtime_status["active_params"] == expected_params)
    )

    from aletheia.files.jsonl import write_audit

    audit = {
        "actor": "human",
        "source": "dashboard",
        "entity_type": "pi_daemon_config",
        "before": old,
        "after": cfg.pi_daemon.model_dump(mode="json"),
        "persisted": True,
        "applied": applied,
        "runtime_status": runtime_status,
    }
    if not applied:
        write_audit({
            **audit,
            "event_type": "pi_daemon_config_apply_failed",
            "message": "System Operator daemon config persisted but runtime apply failed",
        })
        raise HTTPException(
            status_code=503,
            detail={
                "code": "runtime_apply_failed",
                "message": "System Operator daemon configuration was saved, but the runtime state was not applied.",
                "persisted": True,
                "applied": False,
                "runtime_status": runtime_status,
            },
        )

    write_audit({
        **audit,
        "event_type": "pi_daemon_config_changed",
        "message": f"System Operator daemon config applied: {list(updates.keys())}",
    })
    return {
        "updated": updates,
        "persisted": True,
        "applied": True,
        "runtime_status": runtime_status,
    }


# ---------------------------------------------------------------------------
# Serenity Research config (Phase 3, Option A D5 switch)
# ---------------------------------------------------------------------------

class SerenityConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    auto_promote: Optional[bool] = None
    min_score_for_pi: Optional[int] = None
    max_hold_days: Optional[int] = None


_SERENITY_BOUNDS: dict[str, tuple[float, float]] = {
    "min_score_for_pi": (0, 100),
    "max_hold_days": (1, 365),
}


@router.post("/serenity")
def update_serenity_config(req: SerenityConfigUpdate):
    with edit_config() as cfg:
        old = cfg.serenity.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            if field in _SERENITY_BOUNDS:
                lo, hi = _SERENITY_BOUNDS[field]
                if not (lo <= value <= hi):
                    raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
            setattr(cfg.serenity, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "serenity_config_changed",
                 "entity_type": "serenity_config", "before": old,
                 "after": cfg.serenity.model_dump(mode="json"),
                 "message": f"Serenity config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Filter feedback controller config (Phase 1 shadow)
# ---------------------------------------------------------------------------

class ControllerConfigUpdate(SettingsRequest):
    shadow_enabled: Optional[bool] = None
    observe_interval_hours: Optional[int] = None
    min_samples_gate: Optional[int] = None
    max_step: Optional[float] = None


_CONTROLLER_BOUNDS: dict[str, tuple[float, float]] = {
    "observe_interval_hours": (1, 168),
    "min_samples_gate": (5, 500),
    "max_step": (0.01, 0.5),
}


@router.post("/controller")
async def update_controller_config(req: ControllerConfigUpdate):
    with edit_config() as cfg:
        old = cfg.controller.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            if field in _CONTROLLER_BOUNDS:
                lo, hi = _CONTROLLER_BOUNDS[field]
                if not (lo <= value <= hi):
                    raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
            setattr(cfg.controller, field, value)

    _CONTROLLER_SCHEDULER_FIELDS = {"shadow_enabled", "observe_interval_hours"}
    rescheduled = False
    if _CONTROLLER_SCHEDULER_FIELDS.intersection(updates):
        from aletheia.agent_runtime.scheduler import reconcile_filter_controller
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reconcile_filter_controller,
            cfg.controller.shadow_enabled,
            cfg.controller.observe_interval_hours,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "controller_config_changed",
                 "entity_type": "controller_config", "before": old,
                 "after": cfg.controller.model_dump(mode="json"),
                 "message": f"Controller config updated: {list(updates.keys())}"})
    return {"updated": updates, "rescheduled": rescheduled}


# ---------------------------------------------------------------------------
# Paper Learning experiment controls (AT-02)
# ---------------------------------------------------------------------------

class LearningConfigUpdate(SettingsRequest):
    contribution_evaluation_enabled: Optional[bool] = None
    lifecycle_automation_enabled: Optional[bool] = None
    paper_influence_enabled: Optional[bool] = None
    paper_influence_paths: Optional[list[str]] = None
    auto_refreeze: Optional[bool] = None
    evaluation_interval_minutes: Optional[int] = None
    paper_active_allocation_pct: Optional[float] = None


def _lab_experiment_authority() -> bool:
    try:
        from extensions.observation_lab.canonical import get_authority_source

        return get_authority_source().source == "lab"
    except Exception as exc:
        logger.warning("settings: lab authority probe unavailable (%s)", type(exc).__name__)
        return False


def _validated_paper_influence_paths(values: list[str]) -> list[str]:
    requested_paths = sorted({
        str(path).strip() for path in values if str(path).strip()
    })
    from extensions.learning.registry import (
        get_learning_path,
        supported_paper_influence_path_ids,
    )
    unknown_paths = []
    for path_id in requested_paths:
        try:
            get_learning_path(path_id)
        except KeyError:
            unknown_paths.append(path_id)
    if unknown_paths:
        raise HTTPException(
            400, f"unknown paper influence paths: {', '.join(unknown_paths)}",
        )
    unsupported_paths = sorted(
        set(requested_paths) - set(supported_paper_influence_path_ids())
    )
    if unsupported_paths:
        raise HTTPException(
            400,
            "paper influence path is not fully supported for: "
            + ", ".join(unsupported_paths),
        )
    return requested_paths


@router.post("/learning")
async def update_learning_config(req: LearningConfigUpdate):
    with edit_config() as cfg:
        old = cfg.learning.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        if "evaluation_interval_minutes" in updates and not (15 <= int(updates["evaluation_interval_minutes"]) <= 1440):
            raise HTTPException(400, "evaluation_interval_minutes must be between 15 and 1440")
        if "paper_active_allocation_pct" in updates and not (0 < float(updates["paper_active_allocation_pct"]) <= 0.90):
            raise HTTPException(400, "paper_active_allocation_pct must be > 0 and <= 0.90")
        if "paper_influence_paths" in updates:
            updates["paper_influence_paths"] = _validated_paper_influence_paths(
                updates["paper_influence_paths"],
            )
        elif updates.get("paper_influence_enabled") is True:
            # Old versions accepted arbitrary free text.  Merely toggling the master
            # switch must not reactivate one of those unsupported saved values.
            _validated_paper_influence_paths(list(cfg.learning.paper_influence_paths or []))
        for field, value in updates.items():
            setattr(cfg.learning, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "learning_config_changed",
                 "entity_type": "learning_config", "before": old,
                 "after": cfg.learning.model_dump(mode="json"),
                 "message": f"Learning experiment config updated: {list(updates.keys())}"})
    return {
        "updated": updates,
        "rescheduled": False,
        "learning": cfg.learning.model_dump(mode="json"),
    }


@router.get("/learning/experiments")
def list_learning_experiments(limit: int = 100):
    """List all learning experiments with lightweight summary (path, stage, status)."""
    if _lab_experiment_authority():
        from extensions.observation_lab.db import readonly_lab_db

        try:
            with readonly_lab_db(allow_missing=True) as lab:
                if lab is None:
                    raise HTTPException(503, "observation Lab unavailable")
                rows = lab.execute(
                    "SELECT observation_id, generation, kind, status, started_at, spec_json "
                    "FROM observation_windows ORDER BY started_at DESC, observation_id, generation DESC LIMIT ?",
                    (min(max(limit, 1), 200),),
                ).fetchall()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "observation Lab unavailable") from exc
        result = []
        for row in rows:
            try:
                spec = json.loads(row["spec_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                spec = {}
            result.append({
                "experiment_id": f"{row['observation_id']}:g{row['generation']}",
                "hypothesis_id": None,
                "stage": str(spec.get("stage") or row["kind"]),
                "status": row["status"],
                "allocation_pct": spec.get("allocation_pct"),
                "min_samples_per_arm": None,
                "activation_verdict_id": None,
                "created_at": row["started_at"],
                "path_id": spec.get("path_id"),
                "target_horizon": spec.get("horizon") or spec.get("target_horizon"),
            })
        return result
    raise HTTPException(503, "canonical Lab authority unavailable")


@router.get("/learning/experiments/{experiment_id}")
def get_learning_experiment(experiment_id: str):
    """Read-only lifecycle/operator view for one experiment."""
    if _lab_experiment_authority():
        from extensions.observation_lab.db import readonly_lab_db

        try:
            with readonly_lab_db(allow_missing=True) as lab:
                if lab is None:
                    raise HTTPException(503, "observation Lab unavailable")
                lookup_generation = None
                lookup_id = experiment_id
                if ":g" in experiment_id and experiment_id.rsplit(":g", 1)[1].isdigit():
                    lookup_id, raw_generation = experiment_id.rsplit(":g", 1)
                    lookup_generation = int(raw_generation)
                if lookup_generation is None:
                    rows = lab.execute(
                        "SELECT * FROM observation_windows WHERE observation_id=? "
                        "OR substr(observation_id,1,?)=? ORDER BY observation_id, generation DESC",
                        (lookup_id, len(lookup_id), lookup_id),
                    ).fetchall()
                else:
                    rows = lab.execute(
                        "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
                        (lookup_id, lookup_generation),
                    ).fetchall()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "observation Lab unavailable") from exc
        if not rows:
            raise HTTPException(404, f"experiment not found: {experiment_id}")
        if len(rows) > 1:
            active_rows = [candidate for candidate in rows if candidate["status"] == "active"]
            rows = active_rows or rows[:1]
        row = dict(rows[0])
        try:
            spec = json.loads(row.get("spec_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            spec = {}
        try:
            with readonly_lab_db(allow_missing=True) as lab:
                latest_event = lab.execute(
                    "SELECT event_id, action, reason, actor, source_receipt_id, to_status, created_at "
                    "FROM observation_lifecycle_events WHERE observation_id=? AND generation=? "
                    "ORDER BY created_at DESC, event_id DESC LIMIT 1",
                    (row["observation_id"], int(row["generation"])),
                ).fetchone()
        except Exception as exc:
            raise HTTPException(503, "observation Lab unavailable") from exc
        try:
            from aletheia.storage.sqlite import get_db

            owner_receipt = get_db().execute(
                "SELECT receipt_id, lab_application_status, lab_application_error, "
                "lab_applied_observation_id, lab_applied_generation, lab_applied_event_id "
                "FROM observation_delivery_outbox WHERE receipt_kind='owner_action' "
                "AND observation_id=? AND generation=? ORDER BY sequence DESC LIMIT 1",
                (row["observation_id"], int(row["generation"])),
            ).fetchone()
        except Exception as exc:
            raise HTTPException(503, "observation owner-action status unavailable") from exc
        return {
            "available": True,
            "experiment_id": f"{row['observation_id']}:g{row['generation']}",
            "observation_id": row["observation_id"],
            "generation": int(row["generation"]),
            "stage": str(spec.get("stage") or row["kind"]),
            "status": row["status"],
            "spec": spec,
            "terminal_reason": row.get("terminal_reason"),
            "last_error": row.get("last_error"),
            "lifecycle": dict(latest_event) if latest_event else None,
            "owner_action_status": (
                str(owner_receipt["lab_application_status"] or "pending")
                if owner_receipt is not None
                else None
            ),
            "owner_action_receipt_id": (
                str(owner_receipt["receipt_id"]) if owner_receipt is not None else None
            ),
            "owner_action_error": (
                owner_receipt["lab_application_error"] if owner_receipt is not None else None
            ),
            "owner_action_result": (
                {
                    "observation_id": owner_receipt["lab_applied_observation_id"],
                    "generation": owner_receipt["lab_applied_generation"],
                    "event_id": owner_receipt["lab_applied_event_id"],
                }
                if owner_receipt is not None
                and owner_receipt["lab_application_status"] == "applied"
                else None
            ),
        }
    raise HTTPException(503, "canonical Lab authority unavailable")


class AdmitTrialRequest(SettingsRequest):
    verdict_id: str
    expected_policy_epoch: str
    reason: str
    confirm_admission: Literal[True]

    @field_validator("verdict_id", "expected_policy_epoch")
    @classmethod
    def _non_empty_admission_evidence(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("admission evidence must not be empty")
        return normalized

    @field_validator("reason")
    @classmethod
    def _bounded_admission_reason(cls, value: str) -> str:
        normalized = " ".join(str(value).split())
        if len(normalized) < 8:
            raise ValueError("admission reason must be at least 8 characters")
        return normalized[:200]


class ObservationOwnerActionRequest(SettingsRequest):
    """Authenticated-owner intent queued for asynchronous Lab application."""

    action: Literal[
        "withdraw", "refreeze", "invalidate", "revoke",
        "admit", "close", "promote", "hold", "finalize", "adopt",
    ]
    reason: str
    confirm: Literal[True]
    manifest_id: Optional[str] = None
    manifest_hash: Optional[str] = None
    expected_manifest_hash: Optional[str] = None
    expected_generation: Optional[int] = None

    @field_validator("reason")
    @classmethod
    def _bounded_owner_action_reason(cls, value: str) -> str:
        normalized = " ".join(str(value).split())
        if len(normalized) < 8:
            raise ValueError("owner action reason must be at least 8 characters")
        return normalized[:200]

    @field_validator("manifest_id", "manifest_hash", "expected_manifest_hash")
    @classmethod
    def _non_empty_optional_identity(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            raise ValueError("manifest identity must not be empty")
        return normalized

    @field_validator("expected_generation")
    @classmethod
    def _positive_expected_generation(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and int(value) <= 0:
            raise ValueError("expected_generation must be positive")
        return value


@router.post("/learning/experiments/{experiment_id}/admit-trial")
def admit_learning_trial(experiment_id: str, req: AdmitTrialRequest):
    """Admit a shadow experiment to paper_10 trial. Change-management boundary."""
    raise HTTPException(409, "canonical Lab authority requires the Observation owner-action surface")


@router.post("/learning/experiments/{experiment_id}/owner-action")
def queue_observation_owner_action(
    experiment_id: str,
    req: ObservationOwnerActionRequest,
):
    """Queue an owner action and report transport state, never Lab success."""
    if not _lab_experiment_authority():
        raise HTTPException(409, "canonical Lab authority is not selected")
    from extensions.observation_lab.db import readonly_lab_db

    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                raise HTTPException(503, "observation Lab unavailable")
            parsed_generation = None
            parsed_observation = experiment_id
            if ":g" in experiment_id and experiment_id.rsplit(":g", 1)[1].isdigit():
                parsed_observation, raw_generation = experiment_id.rsplit(":g", 1)
                parsed_generation = int(raw_generation)
            if parsed_generation is None:
                rows = lab.execute(
                    "SELECT observation_id, generation, status FROM observation_windows "
                    "WHERE observation_id=? ORDER BY generation DESC",
                    (parsed_observation,),
                ).fetchall()
                if not rows:
                    raise HTTPException(404, f"experiment not found: {experiment_id}")
                if len(rows) > 1:
                    active_rows = [candidate for candidate in rows if candidate["status"] == "active"]
                    rows = active_rows or rows[:1]
                parsed_observation = str(rows[0]["observation_id"])
                parsed_generation = int(rows[0]["generation"])
            else:
                exists = lab.execute(
                    "SELECT 1 FROM observation_windows WHERE observation_id=? AND generation=?",
                    (parsed_observation, parsed_generation),
                ).fetchone()
                if exists is None:
                    raise HTTPException(404, f"experiment not found: {experiment_id}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "observation Lab unavailable") from exc

    from hashlib import sha256
    from extensions.observation_lab.canonical import record_owner_action

    dedup_key = "lab-owner-action:" + sha256(
        json.dumps(
            {
                "observation_id": parsed_observation,
                "generation": parsed_generation,
                "action": req.action,
                "reason": req.reason,
                "manifest_hash": req.manifest_hash or req.expected_manifest_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    try:
        receipt_id = record_owner_action(
            action=req.action,
            actor="dashboard_owner",
            reason=req.reason,
            observation_id=parsed_observation,
            generation=parsed_generation,
            manifest_id=req.manifest_id,
            manifest_hash=req.manifest_hash,
            expected_manifest_hash=req.expected_manifest_hash,
            expected_generation=(
                req.expected_generation
                if req.expected_generation is not None
                else parsed_generation
            ),
            dedup_key=dedup_key,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "owner action receipt unavailable") from exc
    return {
        "status": "pending_lab_application",
        "receipt_id": receipt_id,
        "action": req.action,
        "observation_id": parsed_observation,
        "generation": parsed_generation,
        "message": "owner action receipt persisted; Lab application is asynchronous",
    }







# ---------------------------------------------------------------------------
# Guard / lesson-promotion config
# ---------------------------------------------------------------------------

class RegimeConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    min_hold_periods: Optional[int] = None
    bias_threshold: Optional[float] = None
    short_enabled: Optional[bool] = None


@router.post("/regime")
def update_regime_config(req: RegimeConfigUpdate):
    updates = req.model_dump(exclude_none=True)
    persisted: dict[str, Any] = {}

    def apply_update(cfg) -> bool:
        old = cfg.regime.model_dump(mode="json")
        for field, value in updates.items():
            setattr(cfg.regime, field, value)
        persisted.update({"cfg": cfg, "old": old})
        return bool(updates)

    _mutate_config_with_candidate_publication_permit(apply_update)
    cfg = persisted["cfg"]
    old = persisted["old"]
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "regime_config_changed",
                 "entity_type": "regime_config", "before": old,
                 "after": cfg.regime.model_dump(mode="json"),
                 "message": f"Regime config updated: {list(updates.keys())}"})
    return {"updated": updates}


class GuardConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    enforce_in_paper_ranker: Optional[bool] = None
    max_score_penalty: Optional[float] = None


@router.post("/guard")
def update_guard_config(req: GuardConfigUpdate):
    with edit_config() as cfg:
        old = cfg.guard.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(cfg.guard, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "guard_config_changed",
                 "entity_type": "guard_config", "before": old,
                 "after": cfg.guard.model_dump(mode="json"),
                 "message": f"Guard config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Health monitoring config
# ---------------------------------------------------------------------------

class HealthConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    alert_via_telegram: Optional[bool] = None
    alert_via_windows_toast: Optional[bool] = None
    max_agent_failure_ratio: Optional[float] = None
    llm_unavailable_alert_after: Optional[int] = None
    market_data_max_staleness_min: Optional[int] = None
    supervisor_max_restarts: Optional[int] = None
    supervisor_restart_window_sec: Optional[int] = None
    supervisor_backoff_cap_sec: Optional[int] = None
    self_heal_enabled: Optional[bool] = None
    self_heal_min_interval_sec: Optional[int] = None
    self_heal_max_per_hour: Optional[int] = None
    self_heal_code_drift: Optional[bool] = None
    self_heal_market_data: Optional[bool] = None
    self_heal_port_cleanup: Optional[bool] = None
    require_llm_at_startup: Optional[bool] = None
    startup_gate_max_retries: Optional[int] = None
    startup_gate_retry_delay_sec: Optional[int] = None
    # auto_resume_* were on the HealthConfig schema + returned by GET /health but
    # absent here, so the frontend HealthCard (which POSTs the whole health object
    # back) hit extra="forbid" → every save returned HTTP 422. Declared so health
    # config is saveable AND these gate-recovery params are controllable from UI.
    auto_resume_enabled: Optional[bool] = None
    auto_resume_interval_sec: Optional[int] = None
    exchange_probe_interval_sec: Optional[int] = None
    exchange_probe_failure_threshold: Optional[int] = None
    exchange_auto_resume_interval_sec: Optional[int] = None
    exchange_auto_resume_success_threshold: Optional[int] = None
    # Startup Behavior Gate (see agent_runtime/startup_behavior_gate.py). Declared
    # here so the HealthCard round-trips them; without this, extra="forbid" 422s.
    startup_behavior_gate_enabled: Optional[bool] = None
    startup_gate_recheck_interval_sec: Optional[int] = None


@router.post("/health-config")
def update_health_config(req: HealthConfigUpdate):
    from aletheia.config.schema import HealthConfig
    with edit_config() as cfg:
        old = cfg.health.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        cfg.health = _validated_settings_section(HealthConfig, old, updates, name="HealthConfig")
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "health_config_changed",
                 "entity_type": "health_config", "before": old,
                 "after": cfg.health.model_dump(mode="json"),
                 "message": f"Health config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Retention config
# ---------------------------------------------------------------------------

class RetentionConfigUpdate(SettingsRequest):
    agent_runs_days: Optional[int] = None
    tool_calls_days: Optional[int] = None
    runtime_sessions_days: Optional[int] = None
    run_steps_days: Optional[int] = None
    market_snapshots_days: Optional[int] = None
    portfolio_snapshots_days: Optional[int] = None
    opportunity_ttl_hours: Optional[int] = None
    jsonl_max_mb: Optional[float] = None
    app_db_capacity_mb: Optional[int] = None


_RETENTION_BOUNDS: dict[str, tuple[float, float]] = {
    "agent_runs_days": (1, 90),
    "tool_calls_days": (1, 90),
    "runtime_sessions_days": (1, 90),
    "run_steps_days": (1, 365),
    "market_snapshots_days": (1, 365),
    "portfolio_snapshots_days": (1, 1825),
    "opportunity_ttl_hours": (1, 720),
    "jsonl_max_mb": (10.0, 10000.0),
    "app_db_capacity_mb": (512, 65536),
}


@router.post("/retention")
def update_retention_config(req: RetentionConfigUpdate):
    with edit_config() as cfg:
        old = cfg.retention.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            if field in _RETENTION_BOUNDS:
                lo, hi = _RETENTION_BOUNDS[field]
                if not (lo <= value <= hi):
                    raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
            setattr(cfg.retention, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "retention_config_changed",
                 "entity_type": "retention_config", "before": old,
                 "after": cfg.retention.model_dump(mode="json"),
                 "message": f"Retention config updated: {list(updates.keys())}"})
    return {"updated": updates}


# ---------------------------------------------------------------------------
# Telegram config
# ---------------------------------------------------------------------------

class TelegramConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    allowed_chat_ids: Optional[list[int]] = None
    notify_trade_fills: Optional[bool] = None
    notify_daily_summary: Optional[bool] = None
    daily_summary_hour_cst: Optional[NotificationHour] = None
    dnd_enabled: Optional[bool] = None
    dnd_start_hour: Optional[NotificationHour] = None
    dnd_end_hour: Optional[NotificationHour] = None


@router.post("/telegram")
async def update_telegram_config(req: TelegramConfigUpdate):
    with edit_config() as cfg:
        old = cfg.telegram.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        candidate = _validated_settings_section(
            TelegramConfig,
            old,
            updates,
            name="telegram configuration",
        )
        cfg.telegram = candidate

    rescheduled = False
    if (
        "daily_summary_hour_cst" in updates
        and updates["daily_summary_hour_cst"] != old.get("daily_summary_hour_cst")
    ):
        from aletheia.agent_runtime.scheduler import reschedule_telegram_summary
        from starlette.concurrency import run_in_threadpool
        try:
            rescheduled = await run_in_threadpool(
                reschedule_telegram_summary, cfg.telegram.daily_summary_hour_cst,
            )
        except Exception as exc:
            logger.warning("settings: telegram summary reschedule unavailable (%s)", type(exc).__name__)
            rescheduled = False
        if not rescheduled:
            try:
                with edit_config() as current:
                    current.telegram = TelegramConfig.model_validate(old)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "telegram_config_rollback_failed",
                        "message": "Daily summary rescheduling failed and the previous Telegram configuration could not be restored.",
                    },
                ) from exc
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "telegram_summary_reschedule_failed",
                    "message": "Daily summary rescheduling failed; the previous Telegram configuration was restored.",
                },
            )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "telegram_config_changed",
                 "entity_type": "telegram_config", "before": old,
                 "after": cfg.telegram.model_dump(mode="json"),
                 "message": f"Telegram config updated: {list(updates.keys())}"})
    return {"updated": updates, "rescheduled": rescheduled}


# ---------------------------------------------------------------------------
# Webhook config
# ---------------------------------------------------------------------------

class WebhookConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    url: Optional[str] = None
    secret: Optional[str] = None
    notify_trade_fills: Optional[bool] = None
    notify_alerts: Optional[bool] = None
    notify_daily_summary: Optional[bool] = None
    timeout_sec: Optional[WebhookTimeoutSeconds] = None


@router.post("/webhook")
def update_webhook_config(req: WebhookConfigUpdate):
    with edit_config() as cfg:
        old = cfg.webhook.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        cfg.webhook = _validated_settings_section(
            WebhookConfig,
            old,
            updates,
            name="webhook configuration",
        )
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "webhook_config_changed",
                 "entity_type": "webhook_config", "before": _redacted_webhook(old),
                 "after": _redacted_webhook(cfg.webhook.model_dump(mode="json")),
                 "message": f"Webhook config updated: {list(updates.keys())}"})
    updated = {key: value for key, value in updates.items() if key != "secret"}
    updated["secret_configured"] = bool(cfg.webhook.secret)
    return {"updated": updated}


# ---------------------------------------------------------------------------
# Update config
# ---------------------------------------------------------------------------

class UpdateConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    branch: Optional[str] = None


@router.post("/update-config")
def update_update_config(req: UpdateConfigUpdate):
    with edit_config() as cfg:
        old = {"enabled": cfg.update.enabled, "branch": cfg.update.branch}
        if req.enabled is not None:
            cfg.update.enabled = req.enabled
        if req.branch is not None:
            branch = req.branch.strip()
            if not branch:
                raise HTTPException(400, "branch must not be empty")
            cfg.update.branch = branch
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "update_config_changed",
                 "entity_type": "update_config", "before": old,
                 "after": {"enabled": cfg.update.enabled, "branch": cfg.update.branch},
                 "message": "Update config changed"})
    return {"enabled": cfg.update.enabled, "branch": cfg.update.branch}


@router.post("/permission-mode")
def set_permission_mode(req: PermissionModeRequest):
    if req.mode not in VALID_PERMISSION_MODES:
        raise HTTPException(400, f"Invalid mode. Choose from: {sorted(VALID_PERMISSION_MODES)}")
    with edit_config() as cfg:
        old = cfg.agent.permission_mode
        cfg.agent.permission_mode = req.mode
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "permission_changed",
                 "entity_type": "permission_mode", "before": {"mode": old}, "after": {"mode": req.mode},
                 "message": f"Permission mode {old} -> {req.mode}"})
    return {"old": old, "new": req.mode}




@router.post("/exchange-mode")
def set_exchange_mode(req: ExchangeModeRequest):
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "Mode must be 'demo' or 'live'")
    with edit_config() as cfg:
        old = cfg.exchange.mode
        _reject_live_identity_change(cfg, changed=req.mode != old)
        cfg.exchange.mode = req.mode
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "exchange_mode_changed",
                 "entity_type": "exchange_mode", "before": {"mode": old}, "after": {"mode": req.mode},
                 "message": f"Exchange mode {old} -> {req.mode}"})
    return {"old": old, "new": req.mode}


# ---------------------------------------------------------------------------
# Risk config (editable limits)
# ---------------------------------------------------------------------------

class RiskConfigUpdate(SettingsRequest):
    max_daily_loss_pct: Optional[float] = None
    max_single_trade_risk_pct: Optional[float] = None
    max_total_live_exposure_pct: Optional[float] = None
    max_live_position_margin_usdt: Optional[float] = None
    max_live_limited_cap_usdt: Optional[float] = None
    starting_capital_usdt: Optional[float] = None
    max_demo_position_margin_usdt: Optional[float] = None
    max_per_symbol_entries: Optional[int] = None
    max_demo_single_trade_risk_pct: Optional[float] = None
    max_futures_leverage_live: Optional[int] = None
    max_futures_leverage_demo: Optional[int] = None
    max_leverage_major_demo: Optional[int] = None
    max_leverage_major_live: Optional[int] = None
    max_leverage_mid_demo: Optional[int] = None
    max_leverage_mid_live: Optional[int] = None
    max_leverage_alt_demo: Optional[int] = None
    max_leverage_alt_live: Optional[int] = None
    max_account_leverage_demo: Optional[float] = None
    max_account_leverage_live: Optional[float] = None
    futures_margin_warning_ratio: Optional[float] = None
    futures_margin_force_close_ratio: Optional[float] = None
    min_liquidation_buffer_pct: Optional[float] = None
    max_open_positions_live: Optional[int] = None
    max_open_positions_demo: Optional[int] = None
    allow_short_demo: Optional[bool] = None
    allow_short_live: Optional[bool] = None
    kill_switch_enabled_live: Optional[bool] = None
    kill_switch_enabled_demo: Optional[bool] = None
    correlation_guard_enabled: Optional[bool] = None
    max_sector_concentration_pct: Optional[float] = None
    max_portfolio_beta: Optional[float] = None
    # Scanner-family live-eligibility gate (#14, Position C 2026-07-02)
    live_family_min_samples: Optional[int] = None
    live_family_min_win_rate_pct: Optional[float] = None


# Fields that must never be changed via the dashboard (safety invariants).
_RISK_IMMUTABLE = {"allowed_exchange", "require_risk_check_for_all_orders"}

# Demo/paper capacity controls intentionally have no application-level maximum.
# They remain positive, finite typed values and continue to pass through every
# Risk Engine check; exchange-backed leverage retains venue-compatible bounds.
_RISK_MINIMUMS: dict[str, float] = {
    "max_demo_position_margin_usdt": 1.0,
    "max_demo_single_trade_risk_pct": 0.01,
    "max_account_leverage_demo": 1.0,
    "max_open_positions_demo": 1.0,
}

_RISK_BOUNDS: dict[str, tuple[float, float]] = {
    # 0 disables the daily-loss check; the engine skips the comparison at 0.
    "max_daily_loss_pct": (0.0, 20.0),
    "max_single_trade_risk_pct": (0.01, 10.0),
    "max_total_live_exposure_pct": (1.0, 50.0),
    "max_live_position_margin_usdt": (1.0, 100_000.0),
    "max_live_limited_cap_usdt": (1.0, 100_000.0),
    "starting_capital_usdt": (1.0, 100_000.0),
    # A count of full-size entries, not a currency amount. 0 disables check #19.
    "max_per_symbol_entries": (0, 20),
    "max_futures_leverage_live": (1, 20),
    "max_futures_leverage_demo": (1, 125),
    "max_leverage_major_demo": (1, 20),
    "max_leverage_major_live": (1, 10),
    "max_leverage_mid_demo": (1, 10),
    "max_leverage_mid_live": (1, 5),
    "max_leverage_alt_demo": (1, 5),
    "max_leverage_alt_live": (1, 3),
    "max_account_leverage_live": (1.0, 5.0),
    "futures_margin_warning_ratio": (0.3, 0.95),
    "futures_margin_force_close_ratio": (0.5, 0.99),
    "min_liquidation_buffer_pct": (1.0, 20.0),
    "max_open_positions_live": (1, 50),
    "max_sector_concentration_pct": (10.0, 100.0),
    "max_portfolio_beta": (0.5, 10.0),
    "live_family_min_samples": (1, 500),
    "live_family_min_win_rate_pct": (0.0, 100.0),
}


@router.post("/risk")
def update_risk_config(req: RiskConfigUpdate):
    """Update editable risk limits. Immutable fields are rejected."""
    with edit_config() as cfg:
        old = cfg.risk.model_dump(mode="json")
        old_policy_hashes = {
            mode: risk_policy_hash(getattr(cfg.risk, mode))
            for mode in ("demo", "live")
        }
        updates = req.model_dump(exclude_none=True)
        candidate = cfg.risk.model_copy(deep=True)

        try:
            for field, value in updates.items():
                if field in _RISK_IMMUTABLE:
                    raise HTTPException(400, f"Field '{field}' cannot be modified via the dashboard")
                if field in _RISK_MINIMUMS and value < _RISK_MINIMUMS[field]:
                    raise HTTPException(400, f"'{field}' must be at least {_RISK_MINIMUMS[field]}, got {value}")
                if field in _RISK_BOUNDS:
                    lo, hi = _RISK_BOUNDS[field]
                    if not (lo <= value <= hi):
                        raise HTTPException(400, f"'{field}' must be between {lo} and {hi}, got {value}")
                setattr(candidate, field, value)
            for mode in ("demo", "live"):
                _validate_mode_policy(mode, getattr(candidate, mode))
        except ValidationError as exc:
            raise HTTPException(400, "Invalid risk policy fields") from exc

        for mode in ("demo", "live"):
            policy = getattr(candidate, mode)
            if risk_policy_hash(policy) != old_policy_hashes[mode]:
                policy.revision += 1
        cfg.risk = candidate
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "risk_config_changed",
                 "entity_type": "risk_config", "before": old,
                 "after": cfg.risk.model_dump(mode="json"),
                 "message": f"Risk config updated: {list(updates.keys())}"})
    return {"updated": updates, "risk": cfg.risk.model_dump(mode="json")}


RiskMode = Literal["demo", "live"]


def _policy_payload(cfg) -> dict:
    return {
        mode: {
            **getattr(cfg.risk, mode).model_dump(mode="json"),
            "policy_hash": risk_policy_hash(getattr(cfg.risk, mode)),
        }
        for mode in ("demo", "live")
    }


def _policy_diff(source: dict, target: dict, prefix: str = "") -> list[dict]:
    rows: list[dict] = []
    for key in sorted(set(source) | set(target)):
        path = f"{prefix}.{key}" if prefix else key
        left = source.get(key)
        right = target.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            rows.extend(_policy_diff(left, right, path))
        elif left != right:
            rows.append({"path": path, "source": left, "target": right})
    return rows


def _validate_mode_policy(mode: RiskMode, policy: ModeRiskPolicy) -> None:
    try:
        validate_mode_risk_policy(mode, policy)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class RiskPolicyCopyRequest(SettingsRequest):
    source: RiskMode
    target: RiskMode
    source_revision: int
    target_revision: int
    confirm_target_replacement: bool = False
    reason: str


class RiskPolicyReplaceRequest(SettingsRequest):
    policy: ModeRiskPolicy
    expected_revision: int
    confirm_target_replacement: bool = False
    reason: str


class RiskPolicyResetRequest(SettingsRequest):
    expected_revision: int
    confirm_target_replacement: bool = False
    reason: str


@router.get("/risk-policies")
def get_risk_policies():
    return _policy_payload(load_config())


@router.post("/risk-policies/copy")
def copy_risk_policy(req: RiskPolicyCopyRequest):
    if req.source == req.target:
        raise HTTPException(400, "source and target policies must differ")
    if not req.confirm_target_replacement:
        raise HTTPException(422, "Policy replacement requires explicit confirmation")
    before = None
    replacement = None
    def mutator(cfg):
        nonlocal before, replacement
        source = getattr(cfg.risk, req.source)
        target = getattr(cfg.risk, req.target)
        if source.revision != req.source_revision or target.revision != req.target_revision:
            raise HTTPException(409, "policy_revision_conflict")

        before = target.model_dump(mode="json")
        common = source.model_dump(
            mode="json",
            exclude={"policy_id", "revision", "eligibility"},
        )
        replacement = ModeRiskPolicy.model_validate({
            **target.model_dump(mode="json"),
            **common,
            "revision": target.revision + 1,
        })
        _validate_mode_policy(req.target, replacement)
        setattr(cfg.risk, req.target, replacement)
        return True

    mutate_config(mutator)
    cfg = load_config()
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "risk_policy_copied",
        "entity_type": "risk_policy",
        "entity_id": req.target,
        "before": before,
        "after": replacement.model_dump(mode="json"),
        "message": f"{req.source} policy copied to {req.target}: {req.reason}",
    })
    return _policy_payload(cfg)


@router.put("/risk-policies/{mode}")
def replace_risk_policy(mode: RiskMode, req: RiskPolicyReplaceRequest):
    if not req.confirm_target_replacement:
        raise HTTPException(422, "Policy replacement requires explicit confirmation")
    current = None
    replacement = None
    def mutator(cfg):
        nonlocal current, replacement
        current = getattr(cfg.risk, mode)
        if current.revision != req.expected_revision:
            raise HTTPException(409, "policy_revision_conflict")
        replacement = ModeRiskPolicy.model_validate({
            **req.policy.model_dump(mode="python"),
            "policy_id": current.policy_id,
            "revision": current.revision + 1,
        })
        _validate_mode_policy(mode, replacement)
        setattr(cfg.risk, mode, replacement)
        return True

    mutate_config(mutator)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "risk_policy_replaced",
        "entity_type": "risk_policy",
        "entity_id": mode,
        "before": current.model_dump(mode="json"),
        "after": replacement.model_dump(mode="json"),
        "message": f"{mode} policy replaced: {req.reason}",
    })
    return _policy_payload(load_config())


@router.post("/risk-policies/{mode}/reset-defaults")
def reset_risk_policy(mode: RiskMode, req: RiskPolicyResetRequest):
    if not req.confirm_target_replacement:
        raise HTTPException(422, "Policy replacement requires explicit confirmation")
    current = None
    replacement = None
    def mutator(cfg):
        nonlocal current, replacement
        current = getattr(cfg.risk, mode)
        if current.revision != req.expected_revision:
            raise HTTPException(409, "policy_revision_conflict")
        defaults = default_mode_risk_policy(mode)
        replacement = ModeRiskPolicy.model_validate({
            **defaults.model_dump(mode="python"),
            "policy_id": current.policy_id,
            "revision": current.revision + 1,
        })
        _validate_mode_policy(mode, replacement)
        setattr(cfg.risk, mode, replacement)
        return True

    mutate_config(mutator)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "risk_policy_reset_to_defaults",
        "entity_type": "risk_policy",
        "entity_id": mode,
        "before": current.model_dump(mode="json"),
        "after": replacement.model_dump(mode="json"),
        "message": f"{mode} policy reset to defaults: {req.reason}",
    })
    return _policy_payload(load_config())


# ---------------------------------------------------------------------------
# Symbol filter (whitelist/blacklist mode + single symbol list)
# ---------------------------------------------------------------------------

VALID_SYMBOL_FILTER_MODES = {"whitelist", "blacklist"}


class SymbolsUpdate(SettingsRequest):
    mode: str = "blacklist"
    symbols: list[str] = []


@router.post("/symbols")
def update_tradable_symbols(req: SymbolsUpdate):
    """Update symbol filter mode and list. Risk and exchange are updated atomically."""
    if req.mode not in VALID_SYMBOL_FILTER_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(VALID_SYMBOL_FILTER_MODES)}")
    if req.mode == "whitelist" and not req.symbols:
        raise HTTPException(400, "whitelist mode requires at least one symbol")

    normalized = list(dict.fromkeys(s.strip().upper() for s in req.symbols if s.strip()))

    with edit_config() as cfg:
        old = {"mode": cfg.risk.symbol_filter_mode, "symbols": list(cfg.risk.symbol_list)}

        cfg.risk.symbol_filter_mode = cast(Literal["whitelist", "blacklist"], req.mode)
        cfg.risk.symbol_list = normalized
        cfg.exchange.symbol_list = normalized

        # In whitelist mode, sync observed_symbols so the data layer fetches them.
        from aletheia.data.symbols import to_canonical, InstrumentId, MARKET_SPOT
        if req.mode == "whitelist" and normalized:
            canonical_symbols = [to_canonical(s) for s in normalized]
            cfg.market_data.observed_symbols = canonical_symbols
            exchange = cfg.market_data.ccxt_exchange or "binance"
            cfg.market_data.observed_instruments = [
                str(InstrumentId.from_parts(exchange, sym, MARKET_SPOT))
                for sym in canonical_symbols
            ]


    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "symbols_changed",
                 "entity_type": "symbols", "before": old,
                 "after": {"mode": req.mode, "symbols": normalized},
                 "message": f"Symbol filter updated: mode={req.mode}"})
    return {"mode": req.mode, "symbols": normalized}


# ---------------------------------------------------------------------------
# Observed equities (US equity tickers for equity_observer)
# ---------------------------------------------------------------------------

class EquitiesConfigUpdate(SettingsRequest):
    tickers: list[str]


@router.post("/equities")
def update_observed_equities(req: EquitiesConfigUpdate):
    """Update US equity tickers observed by equity_observer (observation-only, not traded)."""
    normalized = list(dict.fromkeys(t.strip().upper() for t in req.tickers if t.strip()))
    old = []

    def apply_update(cfg):
        old.extend(cfg.market_data.observed_equities)
        cfg.market_data.observed_equities = normalized
        return True

    mutate_config(apply_update)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "equities_config_changed",
                 "entity_type": "market_data_config", "before": {"observed_equities": old},
                 "after": {"observed_equities": normalized},
                 "message": f"Observed equities updated: {old} -> {normalized}"})
    return {"observed_equities": normalized}


# ---------------------------------------------------------------------------
# Trading config (paper position management)
# ---------------------------------------------------------------------------

class MarketAwareExitsUpdate(BaseModel):
    enabled: Optional[bool] = None
    min_hold_hours_before_market_exit: Optional[float] = None
    max_signal_age_min: Optional[float] = None
    urgent_exit_score: Optional[int] = None
    urgent_exit_max_pnl_pct: Optional[float] = None
    tighten_stop_score: Optional[int] = None
    confidence_collapse_score: Optional[int] = None
    tighten_atr_mult: Optional[float] = None
    tighten_pct: Optional[float] = None


class TradingConfigUpdate(SettingsRequest):
    default_stop_loss_pct: Optional[float] = None
    default_take_profit_pct: Optional[float] = None
    max_position_hold_hours: Optional[float] = None
    slippage_bps: Optional[float] = None
    execution_fee_pct: Optional[float] = None
    auto_adopt_enabled: Optional[bool] = None
    trail_enabled: Optional[bool] = None
    trail_atr_mult: Optional[float] = None
    trail_pct: Optional[float] = None
    breakeven_trigger_pct: Optional[float] = None
    default_notional_usdt: Optional[float] = None
    vol_sizing_enabled: Optional[bool] = None
    vol_target_risk_usdt: Optional[float] = None
    vol_target_pct: Optional[float] = None
    vol_kelly_cap_fraction: Optional[float] = None
    exchange_tpsl_enabled: Optional[bool] = None
    persona_sizing_enabled: Optional[bool] = None
    active_persona: Optional[str] = None
    persona_learning_enabled: Optional[bool] = None
    persona_learning_min_samples: Optional[int] = None
    persona_progressive_unlock_enabled: Optional[bool] = None
    persona_unlock_step_samples: Optional[int] = None
    persona_pyramid_enabled: Optional[bool] = None
    persona_pyramid_add_trigger_pct: Optional[float] = None
    persona_auto_advance_enabled: Optional[bool] = None
    market_aware_exits: Optional[MarketAwareExitsUpdate] = None


_TRADING_BOUNDS: dict[str, tuple[float, float]] = {
    "default_stop_loss_pct": (0.1, 50.0),
    "default_take_profit_pct": (0.1, 100.0),
    "max_position_hold_hours": (1.0, 8760.0),
    "slippage_bps": SLIPPAGE_BPS_BOUNDS,
    "execution_fee_pct": EXECUTION_FEE_PCT_BOUNDS,
    "default_notional_usdt": (0.01, 100_000.0),
    "vol_target_risk_usdt": (0.01, 100_000.0),
    "vol_target_pct": (0.1, 100.0),
    "vol_kelly_cap_fraction": (0.01, 0.5),
    "persona_learning_min_samples": (3, 500),
    "persona_unlock_step_samples": (1, 1000),
    "persona_pyramid_add_trigger_pct": (0.1, 50.0),
}
_EXPERIMENTAL_ENABLEMENT_HOLDS = {
    "persona_pyramid_enabled": (
        "Persona pyramid is a policy helper with no production trigger or "
        "risk-approved add-order materializer; new enablement is blocked"
    ),
}


@router.post("/trading")
async def update_trading_config(req: TradingConfigUpdate):
    """Update paper position management parameters."""
    from aletheia.storage import repositories as repo

    # Same lock order as _mutate_config_with_candidate_publication_permit.
    with repo.transaction(immediate=True), edit_config() as cfg:
        old = cfg.trading.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)

        for field, value in updates.items():
            if field == "market_aware_exits" and isinstance(value, dict):
                for k, v in value.items():
                    if v is not None:
                        setattr(cfg.trading.market_aware_exits, k, v)
                continue
            if field == "active_persona":
                if value not in (cfg.trading.personas or {}):
                    raise HTTPException(400, f"active_persona must be one of {sorted(cfg.trading.personas or {})}")
            elif field in _EXPERIMENTAL_ENABLEMENT_HOLDS and value is True:
                raise HTTPException(409, _EXPERIMENTAL_ENABLEMENT_HOLDS[field])
            elif field in _TRADING_BOUNDS:
                lo, hi = _TRADING_BOUNDS[field]
                if not (lo <= value <= hi):
                    raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
            setattr(cfg.trading, field, value)

        old_rollout_enabled = bool(old.get("persona_progressive_unlock_enabled")) and bool(
            old.get("persona_sizing_enabled")
        )
        new_rollout_enabled = bool(cfg.trading.persona_progressive_unlock_enabled) and bool(
            cfg.trading.persona_sizing_enabled
        )
        if old_rollout_enabled and not new_rollout_enabled:
            cfg.trading.persona_rollout_generation += 1
    rollout_error = None
    if old_rollout_enabled and not new_rollout_enabled:
        # Disabling either prerequisite ends the cohort. Re-enabling must not
        # resurrect evidence collected before the off interval.
        from aletheia.trading.persona_rollout import invalidate_persona_rollouts

        if invalidate_persona_rollouts(reason="progressive_rollout_disabled") is None:
            rollout_error = "persona_rollout_invalidation_failed"
    if (
        cfg.trading.persona_progressive_unlock_enabled
        and cfg.trading.persona_sizing_enabled
    ):
        # Reconcile the durable identity on every settings write.  Matching
        # writes are a no-op; any Persona/policy epoch change invalidates the
        # old rollout before a new one can collect evidence.
        from aletheia.trading.persona_rollout import current_persona_rollout

        if current_persona_rollout(cfg, create=True) is None:
            rollout_error = "persona_rollout_creation_failed"
    from aletheia.trading.persona_rollout import persona_rollout_status
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "trading_config_changed",
                 "entity_type": "trading_config", "before": old,
                 "after": cfg.trading.model_dump(mode="json"),
                 "message": f"Trading config updated: {list(updates.keys())}"})
    if rollout_error is not None:
        raise HTTPException(
            503,
            detail={
                "code": rollout_error,
                "config_saved": True,
                "desired_progressive_unlock": new_rollout_enabled,
            },
        )
    rollout_status = await asyncio.to_thread(persona_rollout_status, cfg)
    return {
        "updated": updates,
        "trading": cfg.trading.model_dump(mode="json"),
        "persona_rollout": rollout_status,
    }


# ---------------------------------------------------------------------------
# Market data config
# ---------------------------------------------------------------------------

_SUPPORTED_EXCHANGES = {"binance", "bybit", "okx", "bitget", "gate", "kucoin", "htx"}


class MarketDataConfigUpdate(SettingsRequest):
    ccxt_exchange: Optional[str] = None
    ccxt_fallback: Optional[str] = None
    confirm_exchange_divergence: bool = False
    ohlcv_cache_ttl_min: Optional[int] = None
    enable_yfinance_macro: Optional[bool] = None
    # Tokenized equity/ETF spot symbols on any supported exchange (Bitget r-series,
    # Bitget ON-series, future OKX SWAP).  Replaces the narrower observed_xstocks.
    observed_stock_tokens: Optional[list[str]] = None
    observed_prediction_markets: Optional[list[str]] = None
    # Round 2 macro signals (Step 1-4 zero-cost data integration).
    enable_long_short_ratio: Optional[bool] = None
    enable_fear_greed: Optional[bool] = None
    enable_coingecko_top: Optional[bool] = None
    coingecko_top_n: Optional[int] = None
    derivatives_history_hours: Optional[int] = None
    # Free zero-key data sources (datasrc overhaul).
    enable_hyperliquid: Optional[bool] = None
    derivatives_comparison_venues: Optional[list[DerivativesComparisonVenue]] = Field(default=None, max_length=3)
    enable_defillama: Optional[bool] = None
    # Safe, non-secret scalars from MarketDataConfig; Data credentials are not
    # part of this Core settings surface.
    provider: Optional[str] = None
    enable_crypto_fallback: Optional[bool] = None
    enable_coinalyze: Optional[bool] = None
    prediction_enabled: Optional[bool] = None
    price_divergence_threshold_pct: Optional[float] = None
    price_divergence_alert_enabled: Optional[bool] = None


_MARKET_DATA_BOUNDS: dict[str, tuple[float, float]] = {
    "ohlcv_cache_ttl_min": (1, 60),
    "coingecko_top_n": (10, 250),  # CoinGecko per_page hard ceiling is 250
    "derivatives_history_hours": (0, 168),  # 0 = disabled, up to 7 days backfill
    # #35 fix: divergence-alert threshold percent — 0 disables alerts,
    # 25% is a generous ceiling (anything larger is noise, not a divergence).
    "price_divergence_threshold_pct": (0.0, 25.0),
}

# #35 fix: `provider` must resolve to a known data-source label. Kept in a
# separate set (not part of the numeric bounds) so validation stays explicit.
_MARKET_DATA_PROVIDERS = {"ccxt", "bitget"}


@router.post("/market-data")
def update_market_data_config(req: MarketDataConfigUpdate):
    """Update market data source parameters."""
    cfg = load_config()
    old = cfg.market_data.model_dump(mode="json")
    updates = req.model_dump(
        exclude_none=True, exclude={"confirm_exchange_divergence"},
    )

    validated_updates: dict = {}
    for field, value in updates.items():
        if field in _MARKET_DATA_BOUNDS:
            lo, hi = _MARKET_DATA_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        if field in {"ccxt_exchange", "ccxt_fallback"}:
            value = str(value).strip().lower()
            if value not in _SUPPORTED_EXCHANGES:
                raise HTTPException(400, f"'{field}' must be one of: {sorted(_SUPPORTED_EXCHANGES)}")
        # #35 fix: validate `provider` against the known label set.
        if field == "provider":
            value = str(value).strip().lower()
            if value not in _MARKET_DATA_PROVIDERS:
                raise HTTPException(400, f"'provider' must be one of: {sorted(_MARKET_DATA_PROVIDERS)}")
        if field in {"observed_stock_tokens", "observed_prediction_markets"}:
            value = list(dict.fromkeys(str(s).strip() for s in value if str(s).strip()))
        validated_updates[field] = value

    from aletheia.config.exchange_alignment import exchange_alignment_for_values

    persisted: dict[str, Any] = {}

    def apply_update(latest) -> bool:
        latest_old = latest.market_data.model_dump(mode="json")
        next_data_exchange = str(
            validated_updates.get("ccxt_exchange", latest.market_data.ccxt_exchange),
        )
        latest_alignment = exchange_alignment_for_values(
            paper_execution_mode=latest.exchange.paper_execution_mode,
            data_exchange=next_data_exchange,
            configured_exchange=latest.exchange.name,
        )
        if (
            "ccxt_exchange" in validated_updates
            and next_data_exchange != latest.market_data.ccxt_exchange
        ):
            _require_exchange_divergence_confirmation(
                latest_alignment, req.confirm_exchange_divergence,
            )
        for field, value in validated_updates.items():
            setattr(latest.market_data, field, value)
        persisted.update({
            "cfg": latest,
            "old": latest_old,
            "alignment": latest_alignment,
        })
        return bool(validated_updates)

    _mutate_config_with_candidate_publication_permit(apply_update)
    cfg = persisted["cfg"]
    old = persisted["old"]
    alignment = persisted["alignment"]
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "market_data_config_changed",
                 "entity_type": "market_data_config", "before": old,
                 "after": cfg.market_data.model_dump(mode="json"),
                 "message": f"Market data config updated: {list(validated_updates.keys())}"})
    return {
        "updated": validated_updates,
        "market_data": cfg.market_data.model_dump(mode="json"),
        "exchange_alignment": alignment.to_dict(),
    }


# ---------------------------------------------------------------------------
# Universe config
# ---------------------------------------------------------------------------

class UniverseConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    refresh_interval_min: Optional[int] = None
    max_candidates: Optional[int] = None
    min_volume_24h_usdt: Optional[float] = None
    max_spread_bps: Optional[float] = None
    min_depth_10bps_usd: Optional[float] = None
    snapshot_ttl_enabled: Optional[bool] = None
    snapshot_ttl_hours: Optional[float] = None
    entry_ttl_sec: Optional[int] = None
    candidate_max_continuous_age_sec: Optional[int] = None
    blacklist: Optional[list[str]] = None
    allowlist: Optional[list[str]] = None
    fallback_symbols: Optional[list[str]] = None
    market_type: Optional[str] = None



class CandidateSelectionStatusResponse(TypedDict):
    configured_policy: str
    configured_policy_epoch: str | None
    authority_enabled: bool
    authority_block_reason: str | None
    selection_bases: list[str]




class UniverseConfigUpdateResponse(TypedDict):
    updated: UniverseConfigUpdate
    rescheduled: bool
    universe: UniverseConfig



def _candidate_selection_status(cfg: AppConfig) -> dict[str, Any]:
    from aletheia.universe.candidate_selection_authority import candidate_selection_authority_block_reason
    reason = candidate_selection_authority_block_reason(cfg)
    return {
        "configured_policy": cfg.universe.candidate_selection_policy,
        "configured_policy_epoch": cfg.universe.candidate_selection_policy_epoch,
        "authority_enabled": reason is None,
        "authority_block_reason": reason,
        "selection_bases": ["measured", "startup"],
    }


_UNIVERSE_BOUNDS: dict[str, tuple[float, float]] = {
    "refresh_interval_min": (1, 60),
    "max_candidates": (5, 100),
    "min_volume_24h_usdt": (100_000, 500_000_000),
    "max_spread_bps": FALLBACK_SPREAD_BPS_BOUNDS,
    "min_depth_10bps_usd": (1_000, 10_000_000),
    "snapshot_ttl_hours": (1, 72),
    "entry_ttl_sec": (300, 3600),
    "candidate_max_continuous_age_sec": (300, 86_400),
}


@router.post("/universe", responses={200: {"model": UniverseConfigUpdateResponse}})
def update_universe_config(req: UniverseConfigUpdate):
    """Update universe generator parameters."""
    cfg = load_config()
    old = cfg.universe.model_dump(mode="json")
    updates = req.model_dump(exclude_none=True)
    next_soft = int(updates.get("entry_ttl_sec", old["entry_ttl_sec"]))
    next_hard = int(
        updates.get(
            "candidate_max_continuous_age_sec",
            old["candidate_max_continuous_age_sec"],
        )
    )
    if next_hard < next_soft:
        raise HTTPException(
            400,
            "candidate_max_continuous_age_sec must be >= entry_ttl_sec",
        )

    _VALID_MARKET_TYPES = {"crypto", "stocks", "mixed"}
    normalized_updates: dict[str, Any] = {}
    for field, value in updates.items():
        if field in _UNIVERSE_BOUNDS:
            lo, hi = _UNIVERSE_BOUNDS[field]
            if not (lo <= value <= hi):
                raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
        if field in {"blacklist", "allowlist", "fallback_symbols"}:
            from aletheia.data.symbols import to_canonical
            canonicalized = []
            for s in value:
                s_str = str(s).strip()
                if not s_str:
                    continue
                canonical = to_canonical(s_str)
                if "/" not in canonical:
                    raise HTTPException(400, f"'{s_str}' is not a valid symbol for '{field}'")
                canonicalized.append(canonical)
            value = list(dict.fromkeys(canonicalized))
            if field == "fallback_symbols" and not value:
                raise HTTPException(400, "fallback_symbols must not be empty")
        if field == "market_type" and value not in _VALID_MARKET_TYPES:
            raise HTTPException(400, f"market_type must be one of: {sorted(_VALID_MARKET_TYPES)}")
        normalized_updates[field] = value

    persisted: dict[str, Any] = {}

    def apply_update(latest) -> bool:
        latest_old = latest.universe.model_dump(mode="json")
        latest_soft = int(normalized_updates.get("entry_ttl_sec", latest_old["entry_ttl_sec"]))
        latest_hard = int(normalized_updates.get(
            "candidate_max_continuous_age_sec",
            latest_old["candidate_max_continuous_age_sec"],
        ))
        if latest_hard < latest_soft:
            raise HTTPException(
                400,
                "candidate_max_continuous_age_sec must be >= entry_ttl_sec",
            )
        try:
            replacement = UniverseConfig.model_validate({
                **latest_old,
                **normalized_updates,
            })
        except ValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        latest.universe = replacement
        persisted.update({"cfg": latest, "old": latest_old})
        return bool(normalized_updates)

    _mutate_config_with_candidate_publication_permit(apply_update)
    cfg = persisted["cfg"]
    old = persisted["old"]

    # IntervalTrigger is immutable once added — a saved refresh_interval_min only
    # takes effect at the next gateway restart unless the live job is rescheduled.
    rescheduled = False
    if "refresh_interval_min" in updates and updates["refresh_interval_min"] != old.get("refresh_interval_min"):
        from aletheia.agent_runtime.scheduler import reschedule_universe_refresh
        rescheduled = reschedule_universe_refresh(cfg.universe.refresh_interval_min)

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "universe_config_changed",
                 "entity_type": "universe_config", "before": old,
                 "after": cfg.universe.model_dump(mode="json"),
                 "message": f"Universe config updated: {list(updates.keys())}"})
    return {"updated": normalized_updates, "rescheduled": rescheduled, "universe": cfg.universe.model_dump(mode="json")}


@router.get("/universe/candidate-selection", responses={200: {"model": CandidateSelectionStatusResponse}})
def get_candidate_selection_status():
    """Read the configured Candidate selection authority."""

    from aletheia.universe.candidate_selection_authority import (
        recover_candidate_selection_authority_transition,
    )

    recover_candidate_selection_authority_transition()
    return _candidate_selection_status(load_config())


# ---------------------------------------------------------------------------
# Tunnel config (Cloudflare Tunnel)
# ---------------------------------------------------------------------------

class TunnelConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    tunnel_id: Optional[str] = None
    hostname: Optional[str] = None


class RemoteControlConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    bind_hosts: Optional[list[str]] = None
    require_token: Optional[bool] = None
    api_token: Optional[str] = Field(default=None, min_length=1)


@router.get("/remote-control/interfaces")
def get_remote_control_interfaces():
    from aletheia.gateway.network import discover_ipv4_interfaces
    return {"interfaces": discover_ipv4_interfaces()}


@router.get("/tunnel/status")
async def get_tunnel_status():
    from aletheia.gateway.tunnel import tunnel_status
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(tunnel_status)


@router.post("/tunnel")
def update_tunnel_config(req: TunnelConfigUpdate):
    with edit_config() as cfg:
        old = cfg.tunnel.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            setattr(cfg.tunnel, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "tunnel_config_changed",
                 "entity_type": "tunnel_config", "before": old,
                 "after": cfg.tunnel.model_dump(mode="json"),
                 "message": f"Tunnel config updated: {list(updates.keys())}"})
    tunnel_safe = {k: v for k, v in cfg.tunnel.model_dump(mode="json").items()
                   if k != "credentials_path"}
    return {"updated": updates, "tunnel": tunnel_safe}


@router.post("/remote-control")
def update_remote_control_config(req: RemoteControlConfigUpdate):
    """Save the independent remote-control switch and optional write-only API token."""
    with edit_config() as cfg:
        old_enabled = cfg.remote_control.enabled
        updates = req.model_dump(exclude_none=True)
        api_token = updates.pop("api_token", None)
        if "bind_hosts" in updates:
            from aletheia.gateway.network import normalize_remote_bind_hosts
            try:
                updates["bind_hosts"] = normalize_remote_bind_hosts(updates["bind_hosts"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        effective_enabled = updates.get("enabled", cfg.remote_control.enabled)
        effective_hosts = updates.get("bind_hosts", cfg.remote_control.bind_hosts)
        if effective_enabled and not effective_hosts:
            raise HTTPException(400, "Select at least one network adapter before enabling Remote Control")
        if updates.get("require_token") is True and api_token is None:
            from aletheia.config.secrets import get_secret
            if not (get_secret("remote_control_api_token") or get_secret("tunnel_api_token")):
                raise HTTPException(
                    400,
                    "Set an API token before enabling Remote Control Token protection",
                )
        if api_token is not None:
            from aletheia.config.secrets import set_secret
            set_secret("remote_control_api_token", api_token)
        for field, value in updates.items():
            setattr(cfg.remote_control, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "remote_control_config_changed",
                 "entity_type": "remote_control_config", "before": {"enabled": old_enabled},
                 "after": cfg.remote_control.model_dump(mode="json"),
                 "message": "Remote Control config updated; gateway restart required for listener changes"})
    return {
        "updated": {**updates, "api_token_saved": api_token is not None},
        "restart_required": "enabled" in updates or "bind_hosts" in updates,
        "remote_control": cfg.remote_control.model_dump(mode="json"),
    }


@router.post("/tunnel/start")
async def start_tunnel_api():
    from aletheia.gateway.tunnel import start_tunnel
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(start_tunnel)
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason", "unknown error"))
    return result


@router.post("/tunnel/stop")
async def stop_tunnel_api():
    from aletheia.gateway.tunnel import stop_tunnel
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(stop_tunnel)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", result.get("status", "tunnel stop failed")))
    return result


# ---------------------------------------------------------------------------
# Network / proxy config
# ---------------------------------------------------------------------------

class NetworkConfigUpdate(SettingsRequest):
    # Structured proxy definition — the panel drives these; proxy_url is derived.
    proxy_protocol: Optional[Literal["http", "https", "socks5"]] = None
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    enforce: Optional[bool] = None
    no_proxy: Optional[str] = None
    # Legacy: accepted for backward compatibility (older clients sent only this).
    proxy_url: Optional[str] = None


@router.post("/network")
def update_network_config(req: NetworkConfigUpdate):
    """Update global proxy configuration and apply it to the running process."""
    with edit_config() as cfg:
        old = cfg.network.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        _validated_settings_section(NetworkConfig, old, updates, name="network")
        for field, value in updates.items():
            setattr(cfg.network, field, value)
        # setattr does not re-run the model validator, so keep proxy_url and the
        # structured parts consistent explicitly (structured inputs win; a legacy
        # proxy_url-only update is decomposed back into the parts).
        if {"proxy_protocol", "proxy_host", "proxy_port"} & set(updates):
            cfg.network.recompose_proxy_url()
        elif "proxy_url" in updates and cfg.network.proxy_url:
            cfg.network.parse_proxy_url()
            cfg.network.recompose_proxy_url()

    # Apply immediately: set env vars so trust_env=True clients pick them up.
    from aletheia.net import apply_proxy_env
    apply_proxy_env()

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard",
                 "event_type": "network_config_changed",
                 "entity_type": "network_config", "before": _redacted_network(old),
                 "after": _redacted_network(cfg.network.model_dump(mode="json")),
                 "message": f"Network config updated: {list(updates.keys())}"})
    return {"updated": _redacted_network(updates), "network": _redacted_network(cfg.network.model_dump(mode="json"))}


@router.post("/network/test")
async def test_network_proxy():
    """Test proxy connectivity by pinging Binance API through the configured proxy."""
    import time
    from aletheia.net import httpx_client
    url = "https://api.binance.com/api/v3/ping"
    t0 = time.monotonic()
    try:
        async with httpx_client(timeout=10) as client:
            resp = await client.get(url)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200:
            return {"reachable": True, "latency_ms": latency_ms, "error": None}
        return {"reachable": False, "latency_ms": latency_ms, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"reachable": False, "latency_ms": latency_ms, "error": type(e).__name__}


# ---------------------------------------------------------------------------
# Historical Data config
# ---------------------------------------------------------------------------

class HistoricalDataConfigUpdate(SettingsRequest):
    enabled: bool | None = None
    max_bars_per_pair: int | None = None
    default_months: int | None = None
    nightly_backfill_enabled: bool | None = None
    nightly_backfill_hour_cst: int | None = None
    min_bars_for_backtest: int | None = None
    active_universe_backfill_enabled: bool | None = None
    active_universe_backfill_interval_minutes: int | None = None
    active_universe_backfill_max_pairs_per_run: int | None = None


@router.post("/historical_data")
async def update_historical_data_config(req: HistoricalDataConfigUpdate):
    """Update historical OHLCV data management settings."""
    from aletheia.config.schema import HistoricalDataConfig
    with edit_config() as cfg:
        old = cfg.historical_data.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        cfg.historical_data = _validated_settings_section(HistoricalDataConfig, old, updates, name="HistoricalDataConfig")

    _SCHEDULER_FIELDS = {
        "nightly_backfill_enabled",
        "nightly_backfill_hour_cst",
        "active_universe_backfill_enabled",
        "active_universe_backfill_interval_minutes",
        "enabled",
    }
    rescheduled = False
    if _SCHEDULER_FIELDS.intersection(updates):
        from aletheia.agent_runtime.scheduler import reconcile_historical_data_jobs
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reconcile_historical_data_jobs, cfg.historical_data,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard",
                 "event_type": "historical_data_config_changed",
                 "entity_type": "historical_data_config", "before": old,
                 "after": cfg.historical_data.model_dump(mode="json"),
                 "message": f"Historical data config updated: {list(updates.keys())}"})
    return {"updated": updates, "rescheduled": rescheduled, "historical_data": cfg.historical_data.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Event Trigger config
# ---------------------------------------------------------------------------

class EventTriggerConfigUpdate(SettingsRequest):
    enabled: bool | None = None
    price_move_pct_threshold: float | None = None
    volume_surge_multiplier: float | None = None
    watch_universe_members: bool | None = None
    # trigger_strategy_design and strategy_design_min_move_pct removed: DEAD CONFIG.
    # strategy_designer was removed with Position C (2026-07-02). Callers that send
    # these fields receive a 422 — the extra="forbid" SettingsRequest policy is
    # intentionally strict so UI/API mismatches fail loudly rather than silently.


_EVENT_TRIGGER_BOUNDS: dict[str, tuple[float, float]] = {
    "price_move_pct_threshold": (0.5, 20.0),
    "volume_surge_multiplier": (1.5, 20.0),
}


@router.post("/event_triggers")
def update_event_trigger_config(req: EventTriggerConfigUpdate):
    """Update event-driven trigger thresholds."""
    with edit_config() as cfg:
        old = cfg.event_triggers.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True)
        for field, value in updates.items():
            if field in _EVENT_TRIGGER_BOUNDS:
                lo, hi = _EVENT_TRIGGER_BOUNDS[field]
                if not (lo <= value <= hi):
                    raise HTTPException(400, f"'{field}' must be between {lo} and {hi}")
            setattr(cfg.event_triggers, field, value)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard",
                 "event_type": "event_trigger_config_changed",
                 "entity_type": "event_trigger_config", "before": old,
                 "after": cfg.event_triggers.model_dump(mode="json"),
                 "message": f"Event trigger config updated: {list(updates.keys())}"})
    return {"updated": updates, "event_triggers": cfg.event_triggers.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# News feeds config
# ---------------------------------------------------------------------------

class NewsFeedUpdate(SettingsRequest):
    name: str
    url: str
    source: str
    enabled: bool


class NewsConfigUpdate(SettingsRequest):
    enabled: Optional[bool] = None
    fetch_interval_minutes: Optional[int] = None
    retention_days: Optional[int] = None
    feeds: Optional[list[NewsFeedUpdate]] = None


@router.post("/news")
async def update_news_config(req: NewsConfigUpdate):
    from aletheia.config.schema import NewsConfig
    with edit_config() as cfg:
        old = cfg.news.model_dump(mode="json")
        updates = req.model_dump(exclude_none=True, exclude={"feeds"})
        cfg.news = _validated_settings_section(NewsConfig, old, updates, name="NewsConfig")
        if req.feeds is not None:
            from aletheia.config.schema import NewsFeedEntry
            cfg.news.feeds = [NewsFeedEntry(**f.model_dump()) for f in req.feeds]

    rescheduled = False
    if (
        req.fetch_interval_minutes is not None
        and req.fetch_interval_minutes != old.get("fetch_interval_minutes")
    ):
        from aletheia.agent_runtime.scheduler import reschedule_news_fetch
        from starlette.concurrency import run_in_threadpool
        rescheduled = await run_in_threadpool(
            reschedule_news_fetch, cfg.news.fetch_interval_minutes,
        )

    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "news_config_changed",
                 "entity_type": "news_config", "before": old,
                 "after": cfg.news.model_dump(mode="json"),
                 "message": "News config updated"})
    return {"news": cfg.news.model_dump(mode="json"), "rescheduled": rescheduled}
