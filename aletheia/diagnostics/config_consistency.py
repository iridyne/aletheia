"""Cross-field config consistency checks.

Pure read-only validation: given an AletheiaConfig, returns a list of
ConfigInconsistency findings. Never raises; each check is isolated.
"""

from __future__ import annotations

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
        _check_whitelist_empty,
        _check_scheduler_needs_llm,
        _check_live_state,
    ]
    for check in checks:
        try:
            finding = check(cfg)
            if finding:
                results.append(finding)
        except Exception:  # noqa: BLE001 — single check failure never sinks the rest
            pass
    return results


def _check_default_provider(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    default = cfg.models.default_provider
    providers = cfg.models.providers
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
    if cfg.exchange.mode != "demo":
        return None
    if cfg.exchange.paper_execution_mode != "bitget_demo":
        return None
    # Check if demo API key exists in secrets store
    try:
        from aletheia.auth.secrets import get_secret
        key = get_secret("bitget_api_key_demo") or get_secret("BITGET_API_KEY_DEMO")
        if not key:
            return ConfigInconsistency(
                field_path="exchange.paper_execution_mode",
                severity="warning",
                message="paper_execution_mode=bitget_demo but demo API credentials are missing",
                fix_hint="Set BITGET_API_KEY_DEMO / BITGET_API_SECRET_DEMO / BITGET_PASSPHRASE_DEMO in settings, or switch paper_execution_mode to 'paper_internal'",
            )
    except Exception:  # noqa: BLE001 — secrets layer might not be available
        pass
    return None


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
    default = cfg.models.default_provider
    providers = cfg.models.providers
    if default not in providers or not providers[default].enabled:
        return ConfigInconsistency(
            field_path="runtime.scheduler_enabled",
            severity="warning",
            message=f"scheduler_enabled=true but default LLM provider '{default}' is unavailable — agent runs will fail",
            fix_hint=f"Enable the '{default}' provider, or update default_provider to an available one",
        )
    return None


def _check_live_state(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    if cfg.exchange.mode != "live":
        return None
    if cfg.live.state == "live_disabled":
        return ConfigInconsistency(
            field_path="live.state",
            severity="warning",
            message="exchange.mode=live but live.state=live_disabled — live orders are blocked",
            fix_hint="Set live.state to 'live_enabled' to allow live trading, or keep it disabled intentionally",
        )
    return None
