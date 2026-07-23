"""Resolve the authoritative risk policy from a proposal execution mode."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from aletheia.config.schema import ModeRiskPolicy


DEMO_EXECUTION_MODES = frozenset({
    "paper_internal",
    "bitget_demo",
    "binance_testnet",
    "okx_demo",
})
LIVE_EXECUTION_MODES = frozenset({
    "bitget_live_limited",
    "binance_live_limited",
    "okx_live_limited",
})


class UnknownRiskExecutionMode(ValueError):
    """Raised when no registered risk policy owns an execution mode."""


@dataclass(frozen=True)
class ResolvedRiskPolicy:
    mode: Literal["demo", "live"]
    policy: ModeRiskPolicy
    policy_hash: str

    def legacy_view(self, shared_risk) -> "RiskPolicyView":
        return RiskPolicyView(self.policy, shared_risk)


class RiskPolicyView:
    """Compatibility view backed authoritatively by one nested mode policy."""

    def __init__(self, policy: ModeRiskPolicy, shared_risk):
        self._policy = policy
        self._shared = shared_risk

    def __getattr__(self, name: str):
        mapped = {
            "max_live_limited_cap_usdt": self._policy.capital.base_usdt,
            "starting_capital_usdt": self._policy.capital.base_usdt,
            "max_live_position_notional_usdt": self._policy.capital.max_position_notional_usdt,
            "max_demo_position_notional_usdt": self._policy.capital.max_position_notional_usdt,
            "max_per_symbol_notional_usdt": self._policy.capital.max_per_symbol_notional_usdt,
            "max_total_live_exposure_pct": self._policy.capital.max_total_exposure_pct,
            "max_daily_loss_pct": self._policy.loss.max_daily_loss_pct,
            "max_single_trade_risk_pct": self._policy.loss.max_single_trade_risk_pct,
            "max_demo_single_trade_risk_pct": self._policy.loss.max_single_trade_risk_pct,
            "max_open_positions_live": self._policy.positions.max_open_positions,
            "max_open_positions_demo": self._policy.positions.max_open_positions,
            "allow_short_live": self._policy.positions.allow_short,
            "allow_short_demo": self._policy.positions.allow_short,
            "max_futures_leverage_live": self._policy.leverage.futures_cap,
            "max_futures_leverage_demo": self._policy.leverage.futures_cap,
            "max_leverage_major_live": self._policy.leverage.major_cap,
            "max_leverage_major_demo": self._policy.leverage.major_cap,
            "max_leverage_mid_live": self._policy.leverage.mid_cap,
            "max_leverage_mid_demo": self._policy.leverage.mid_cap,
            "max_leverage_alt_live": self._policy.leverage.alt_cap,
            "max_leverage_alt_demo": self._policy.leverage.alt_cap,
            "max_account_leverage_live": self._policy.leverage.account_cap,
            "max_account_leverage_demo": self._policy.leverage.account_cap,
            "futures_margin_warning_ratio": self._policy.margin.warning_ratio,
            "futures_margin_force_close_ratio": self._policy.margin.block_ratio,
            "min_liquidation_buffer_pct": self._policy.margin.min_entry_liquidation_buffer_pct,
            "runtime_liquidation_buffer_pct": self._policy.margin.runtime_liquidation_buffer_pct,
            "correlation_guard_enabled": self._policy.correlation.enabled,
            "max_sector_concentration_pct": self._policy.correlation.max_sector_concentration_pct,
            "max_portfolio_beta": self._policy.correlation.max_portfolio_beta,
            "catastrophic_stop_enabled": self._policy.exits.catastrophic_stop_enabled,
            "catastrophic_stop_pct": self._policy.exits.catastrophic_stop_pct,
            "catastrophic_stop_pct_serenity": self._policy.exits.catastrophic_stop_pct_serenity,
            "horizon_hard_backstop_mult": self._policy.exits.horizon_hard_backstop_mult,
            "kill_switch_enabled_live": self._policy.auto_trip_mode_brake_on_daily_loss,
            "kill_switch_enabled_demo": self._policy.auto_trip_mode_brake_on_daily_loss,
        }
        eligibility = self._policy.eligibility
        if eligibility is not None:
            mapped.update({
                "live_family_min_samples": eligibility.family_min_samples,
                "live_family_min_win_rate_pct": eligibility.family_min_win_rate_pct,
            })
        if name in mapped:
            return mapped[name]
        return getattr(self._shared, name)


def risk_policy_hash(policy: ModeRiskPolicy) -> str:
    raw = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def resolve_risk_policy(cfg, execution_mode: str) -> ResolvedRiskPolicy:
    if execution_mode in DEMO_EXECUTION_MODES:
        mode: Literal["demo", "live"] = "demo"
    elif execution_mode in LIVE_EXECUTION_MODES:
        mode = "live"
    else:
        raise UnknownRiskExecutionMode(
            f"unregistered risk execution mode: {execution_mode!r}"
        )
    policy = getattr(cfg.risk, mode)
    return ResolvedRiskPolicy(
        mode=mode,
        policy=policy,
        policy_hash=risk_policy_hash(policy),
    )
