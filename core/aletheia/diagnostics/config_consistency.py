"""Cross-field config consistency checks.

Pure read-only validation: given an AletheiaConfig, returns a list of
ConfigInconsistency findings. Never raises; each check is isolated.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.diagnostics.config_consistency")

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aletheia.config.schema import AletheiaConfig


@dataclass
class ConfigInconsistency:
    field_path: str     # e.g. "models.default_provider"
    severity: str       # "warning" | "blocker"
    message: str
    fix_hint: str


def check_config_consistency(cfg: "AletheiaConfig") -> list[ConfigInconsistency]:
    """Run all cross-field consistency checks. Returns findings (empty = all OK)."""
    results: list[ConfigInconsistency] = []
    checks = [
        _check_default_provider,
        _check_demo_credentials,
        _check_exchange_consistency,
        _check_whitelist_empty,
        _check_scheduler_needs_llm,
        _check_live_state,
        _check_risk_caps_admit_sizing,
    ]
    for check in checks:
        try:
            finding = check(cfg)
            if finding:
                results.append(finding)
        except Exception:  # noqa: BLE001 — single check failure never sinks the rest
            logger.warning("config_consistency: a consistency check raised; its finding is missing from the report", exc_info=True)
    return results


def _check_default_provider(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    default = cfg.models.default_provider
    providers = cfg.models.providers
    if not default:
        return None
    if default not in providers:
        return ConfigInconsistency(
            field_path="models.default_provider",
            severity="warning",
            message=f"default_provider='{default}' is not in providers list",
            fix_hint=f"Add a '{default}' entry under models.providers, or change default_provider to one of: {list(providers.keys())}",
        )
    p = providers[default]
    if not p.enabled:
        return ConfigInconsistency(
            field_path="models.default_provider",
            severity="warning",
            message=f"default_provider='{default}' is disabled (enabled=false)",
            fix_hint=f"Enable the '{default}' provider or change default_provider to an enabled one",
        )
    return None


def _check_demo_credentials(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    from aletheia.config.exchanges import DEMO_EXECUTION_MODES
    from aletheia.uta.manager import build_uta_registry

    mode = cfg.exchange.paper_execution_mode
    if cfg.exchange.mode != "demo" or mode not in DEMO_EXECUTION_MODES:
        return None
    try:
        registry = build_uta_registry(cfg, execution_modes=(mode,))
        if registry.configured_identities():
            return None
        detail = "demo account credentials are incomplete"
    except Exception:  # noqa: BLE001 — unavailable is not configured
        detail = "demo account credential configuration is unavailable"
    return ConfigInconsistency(
        field_path="exchange.paper_execution_mode",
        severity="warning",
        message=f"paper_execution_mode={mode}: {detail}",
        fix_hint="Check the selected account credentials via Settings → Credentials",
    )


def _check_exchange_consistency(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    from aletheia.config.exchange_alignment import exchange_alignment

    alignment = exchange_alignment(cfg)
    if not alignment.diverged:
        return None
    return ConfigInconsistency(
        field_path="market_data.ccxt_exchange",
        severity="info",
        message=(
            f"Order execution ({alignment.execution_exchange}) and market data "
            f"({alignment.data_exchange}) use different exchanges"
        ),
        fix_hint=(
            "Use Settings → Primary Exchange to unify, or keep the persistent "
            "dashboard warning if the split is intentional"
        ),
    )


def _check_whitelist_empty(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    if cfg.risk.symbol_filter_mode != "whitelist":
        return None
    risk_list = cfg.risk.symbol_list or []
    exchange_list = cfg.exchange.symbol_list or []
    if not risk_list and not exchange_list:
        return ConfigInconsistency(
            field_path="risk.symbol_list",
            severity="warning",
            message="symbol_filter_mode=whitelist but symbol_list is empty — no symbols can trade",
            fix_hint="Add symbols to risk.symbol_list or exchange.symbol_list, or switch symbol_filter_mode to 'blacklist'",
        )
    return None


def _check_scheduler_needs_llm(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    if not cfg.runtime.scheduler_enabled:
        return None
    from aletheia.model_provider.router import provider_is_configured

    unavailable = []
    for tier in ("fast", "deep"):
        selected = getattr(cfg.models, tier)
        profile = cfg.models.providers.get(selected.endpoint_id) if selected else None
        if (
            selected is None
            or not selected.endpoint_id.strip()
            or not selected.model_id.strip()
            or profile is None
            or not profile.enabled
            or not provider_is_configured(selected.endpoint_id, profile)
        ):
            unavailable.append(tier)
    if unavailable:
        return ConfigInconsistency(
            field_path="runtime.scheduler_enabled",
            severity="warning",
            message=f"scheduler_enabled=true but model selections are unavailable: {', '.join(unavailable)} — affected LLM tasks cannot run",
            fix_hint="Assign each affected task tier an explicit model on an enabled HTTP endpoint in Settings → AI Model Providers",
        )
    return None


def _check_live_state(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    if cfg.exchange.mode != "live":
        return None
    if cfg.live.state == "live_disabled":
        return ConfigInconsistency(
            field_path="live.state",
            severity="warning",
            message=(
                "exchange.mode=live requests experimental readiness, but "
                "live.state=live_disabled; new proposals still use the configured "
                "paper/demo execution mode"
            ),
            fix_hint=(
                "Keep live disabled or use the audited Live Unlock prerequisite. "
                "Unlocking does not enable live proposal routing (GitHub #418)"
            ),
        )
    # EX-04 (#1004): live.state=live_limited authorizes proposal-mode selection
    # only while a valid live_authorization_ref is currently in scope. When
    # unlock evidence has expired, been revoked, or does not match the selected
    # account/config, new proposals stay on the paper/demo path fail-closed.
    try:
        from aletheia.trust.live_unlock import current_authorization_ref

        authorized = current_authorization_ref(cfg) is not None
    except Exception as exc:
        logger.warning("config_consistency: live authorization probe unavailable (%s)", type(exc).__name__)
        authorized = False
    if authorized:
        return None
    return ConfigInconsistency(
        field_path="exchange.mode",
        severity="warning",
        message=(
            "experimental Live authority is unlocked, but no valid live "
            "authorization is currently in scope; new proposals still use "
            f"exchange.paper_execution_mode={cfg.exchange.paper_execution_mode}"
        ),
        fix_hint=(
            "Re-run the Live Unlock preflight so the current unlock evidence "
            "matches the selected account/config and has not expired "
            "(GitHub #418)"
        ),
    )


def _check_risk_caps_admit_sizing(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    """Detect when risk caps would block every first entry at current account size."""
    try:
        from aletheia.risk.engine import account_available_for_execution, per_symbol_notional_ceiling

        mode = cfg.exchange.paper_execution_mode
        available = account_available_for_execution(mode)
        if not available or available <= 0:
            return None

        from aletheia.risk.policy import LIVE_EXECUTION_MODES
        is_live = mode in LIVE_EXECUTION_MODES
        risk_policy = cfg.risk.live if is_live else cfg.risk.demo
        risk_pct = float(risk_policy.loss.max_single_trade_risk_pct)
        leverage = float(risk_policy.leverage.major_cap)

        # Notional = available × risk_pct × leverage; margin = notional / leverage = available × risk_pct
        notional_entry = available * (risk_pct / 100.0) * leverage
        margin_entry = available * (risk_pct / 100.0)
        if margin_entry <= 0:
            return None

        ceiling = per_symbol_notional_ceiling(mode, leverage=leverage, cfg=cfg)
        if ceiling is None:
            return ConfigInconsistency(
                field_path="risk.capital.max_per_symbol_entries",
                severity="blocker",
                message=(
                    "Per-symbol ceiling could not be resolved; check #19's bound "
                    "cannot be verified for current sizing"
                ),
                fix_hint="Confirm risk policy resolution and account equity are available",
            )
        if ceiling > 0 and notional_entry > ceiling:
            return ConfigInconsistency(
                field_path="risk.capital.max_per_symbol_entries",
                severity="blocker",
                message=(
                    f"Sizing output ({notional_entry:.0f}) exceeds per-symbol ceiling "
                    f"({ceiling:.0f}) — check #19 will reject every first entry"
                ),
                fix_hint="Increase max_per_symbol_entries, or reduce max_single_trade_risk_pct / major leverage",
            )

        pos_cap = float(risk_policy.capital.max_position_margin_usdt)
        if pos_cap > 0 and margin_entry > pos_cap:
            return ConfigInconsistency(
                field_path="risk.capital.max_position_margin_usdt",
                severity="blocker",
                message=(
                    f"Margin required ({margin_entry:.0f}) exceeds position margin cap "
                    f"({pos_cap:.0f}) — check #5 will reject every entry"
                ),
                fix_hint=f"Raise max_position_margin_usdt above {margin_entry:.0f}",
            )
    except Exception:
        logger.warning("config_consistency: risk-cap sizing check raised; no finding reported for this config", exc_info=True)
    return None
