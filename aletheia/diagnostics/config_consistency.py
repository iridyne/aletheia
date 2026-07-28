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
        _check_exchange_consistency,
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


# NOTE: credentials are loaded via get_secret() (keyring / encrypted store), NOT from
# environment variables — so the fix hints must point at the secrets store, not env vars.
_DEMO_CRED_MAP: dict[str, tuple[str, str]] = {
    "bitget_demo": ("bitget.demo.api_key", "Configure demo credentials via Settings → Credentials (or `aletheia secrets set bitget.demo.api_key` / `.api_secret` / `.passphrase`)"),
    "okx_demo": ("okx.demo.api_key", "Configure demo credentials via Settings → Credentials (or `aletheia secrets set okx.demo.api_key` / `.api_secret` / `.passphrase`)"),
    "binance_testnet": ("binance.testnet.api_key", "Configure testnet credentials via Settings → Credentials (or `aletheia secrets set binance.testnet.api_key` / `.api_secret`)"),
}


def _check_demo_credentials(cfg: "AletheiaConfig") -> ConfigInconsistency | None:
    if cfg.exchange.mode != "demo":
        return None
    pem = cfg.exchange.paper_execution_mode
    if pem not in _DEMO_CRED_MAP:
        return None
    secret_key, fix_hint = _DEMO_CRED_MAP[pem]
    try:
        from aletheia.config.secrets import get_secret
        key = get_secret(secret_key)
        if not key:
            return ConfigInconsistency(
                field_path="exchange.paper_execution_mode",
                severity="warning",
                message=f"paper_execution_mode={pem} but demo API credentials are missing",
                fix_hint=f"{fix_hint}, or switch paper_execution_mode to 'paper_internal'",
            )
    except Exception:  # noqa: BLE001 — secrets layer might not be available
        pass
    return None


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
    return ConfigInconsistency(
        field_path="exchange.mode",
        severity="warning",
        message=(
            "experimental Live authority is unlocked, but new proposals still use "
            f"exchange.paper_execution_mode={cfg.exchange.paper_execution_mode}"
        ),
        fix_hint=(
            "Use exchange.mode=demo for normal operation; keep live selected only "
            "when evaluating readiness until GitHub #418 is implemented"
        ),
    )
