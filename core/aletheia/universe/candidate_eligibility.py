"""Pure Candidate Selection Utility hard-gate contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from numbers import Real
from typing import Literal, Mapping

from aletheia.utils.dt import parse_utc_instant
from aletheia.config.schema import UniverseConfig


HARD_GATE_VERSION = "universe-hard-gate/v1"
HARD_GATE_POLICY_VERSION = "universe-hard-gate-policy/v2"
_MAX_FRESHNESS_SEC = 7200.0
_STABLECOINS = frozenset({
    "USDC", "USDT", "DAI", "TUSD", "BUSD", "FDUSD", "USDP", "PYUSD",
    "USD1", "USDD", "FRAX", "LUSD", "GUSD",
})


def canonical_fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _finite(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


@dataclass(frozen=True)
class HardGateCheckV1:
    check_id: str
    status: Literal["passed", "failed", "unavailable", "not_applicable"]
    observed_value: object = None
    threshold: object = None
    reason_code: str = ""


@dataclass(frozen=True)
class HardGateDecisionV1:
    version: Literal["universe-hard-gate/v1"]
    candidate_key: str
    evaluated_at: str
    instrument: Mapping[str, str]
    status: Literal["eligible", "ineligible", "unavailable"]
    actionability: Literal["actionable", "observe_only", "none"]
    checks: tuple[HardGateCheckV1, ...]
    reason_codes: tuple[str, ...]
    input_fingerprint: str
    policy_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_hard_gate(
    feature: Mapping[str, object],
    cfg: UniverseConfig,
    *,
    candidate_key: str,
    evaluated_at: str,
    action_route_reason: str | None = None,
) -> HardGateDecisionV1:
    """Evaluate physical/data eligibility without assigning Alpha points."""

    venue = str(feature.get("venue") or "").strip().lower()
    symbol = str(feature.get("symbol") or "").strip().upper()
    market_type = str(feature.get("market_type") or "").strip().lower()
    if market_type in {"swap", "futures", "usdt_futures"}:
        market_type = "perp"
    instrument = {"venue": venue, "symbol": symbol, "market_type": market_type}
    checks: list[HardGateCheckV1] = []

    registry_status = feature.get("registry_status")
    if registry_status is None:
        checks.append(HardGateCheckV1("market_active", "unavailable", reason_code="market_active_unavailable"))
    elif str(registry_status) != "active":
        checks.append(HardGateCheckV1("market_active", "failed", registry_status, "active", "market_inactive"))
    else:
        checks.append(HardGateCheckV1("market_active", "passed", registry_status, "active", "market_active"))

    identity_complete = bool(
        venue and symbol and market_type in {"spot", "perp"}
        and feature.get("asset_id") and feature.get("feature_snapshot_id")
    )
    checks.append(HardGateCheckV1(
        "identity", "passed" if identity_complete else "unavailable",
        instrument, reason_code="exact_identity_available" if identity_complete else "exact_identity_unavailable",
    ))

    base, separator, quote = symbol.partition("/")
    supported_quote = separator == "/" and quote.split(":", 1)[0] == "USDT"
    if not supported_quote:
        checks.append(HardGateCheckV1("stablecoin", "failed", symbol, "*/USDT", "unsupported_quote_or_symbol"))
    elif base in _STABLECOINS:
        checks.append(HardGateCheckV1("stablecoin", "failed", base, reason_code="stablecoin_base_excluded"))
    else:
        checks.append(HardGateCheckV1("stablecoin", "passed", base, reason_code="non_stablecoin_base"))

    # An empty allowlist stays unrestricted. A configured one is the outer bound
    # of the tradable universe, so it is answered before the exclusion list.
    allowlist = {str(item).upper() for item in getattr(cfg, "allowlist", None) or ()}
    admitted_by_allowlist = not allowlist or symbol in allowlist
    checks.append(HardGateCheckV1(
        "allowlist", "passed" if admitted_by_allowlist else "failed",
        admitted_by_allowlist, bool(allowlist),
        "allowlist_unrestricted" if not allowlist
        else ("allowlisted" if admitted_by_allowlist else "not_allowlisted"),
    ))

    blacklisted = symbol in {str(item).upper() for item in cfg.blacklist}
    checks.append(HardGateCheckV1(
        "blacklist", "failed" if blacklisted else "passed", blacklisted, False,
        "configured_blacklist" if blacklisted else "not_blacklisted",
    ))

    price_raw = feature.get("last_price", feature.get("price"))
    price = _finite(price_raw, positive=True)
    checks.append(HardGateCheckV1(
        "price", "passed" if price is not None else ("unavailable" if price_raw is None else "failed"),
        price_raw, 0.0, "price_available" if price is not None else ("price_unavailable" if price_raw is None else "price_invalid"),
    ))

    provenance = {
        "feature_snapshot_id": feature.get("feature_snapshot_id"),
        "source_timestamp": feature.get("feature_snapshot_ts"),
        "source": feature.get("feature_source"),
    }
    source_time = parse_utc_instant(provenance["source_timestamp"])
    evaluation_time = parse_utc_instant(evaluated_at)
    provenance_available = bool(
        provenance["feature_snapshot_id"]
        and provenance["source"] == "universe_feature_snapshot/v1"
        and source_time is not None
        and evaluation_time is not None
        and source_time <= evaluation_time
    )
    checks.append(HardGateCheckV1(
        "provenance", "passed" if provenance_available else "unavailable", provenance,
        reason_code="source_provenance_available" if provenance_available else "source_provenance_unavailable",
    ))

    def numeric_check(check_id: str, key: str, threshold: float, *, maximum: bool = False) -> None:
        raw = feature.get(key)
        value = _finite(raw)
        unavailable = raw is None or value is None or value < 0
        failed = False if unavailable else (value > threshold if maximum else value < threshold)
        if unavailable:
            status, reason = "unavailable", f"{check_id}_unavailable"
        elif failed:
            status, reason = "failed", f"{check_id}_{'above_maximum' if maximum else 'below_minimum'}"
        else:
            status, reason = "passed", f"{check_id}_{'within_limit' if maximum else 'sufficient'}"
        checks.append(HardGateCheckV1(check_id, status, raw, threshold, reason))

    numeric_check("volume", "quote_volume_24h", float(cfg.min_volume_24h_usdt))
    numeric_check("spread", "spread_bps", float(cfg.max_spread_bps) * 2.0, maximum=True)
    numeric_check("depth", "depth_10bps_usd", float(cfg.min_depth_10bps_usd) * 0.1)
    numeric_check("freshness", "data_freshness_sec", _MAX_FRESHNESS_SEC, maximum=True)

    checks.append(HardGateCheckV1(
        "action_route", "passed" if action_route_reason is None else "unavailable",
        action_route_reason, None,
        "action_route_available" if action_route_reason is None else f"action_route_unavailable:{action_route_reason}",
    ))

    physical_checks = [item for item in checks if item.check_id != "action_route"]
    if any(item.status == "failed" for item in physical_checks):
        status, actionability = "ineligible", "none"
    elif any(item.status == "unavailable" for item in physical_checks):
        status, actionability = "unavailable", "none"
    elif action_route_reason is not None:
        status, actionability = "eligible", "observe_only"
    else:
        status, actionability = "eligible", "actionable"
    policy = {
        "version": HARD_GATE_POLICY_VERSION,
        "min_volume_24h_usdt": cfg.min_volume_24h_usdt,
        "max_spread_bps": float(cfg.max_spread_bps) * 2.0,
        "critical_depth_usd": cfg.min_depth_10bps_usd * 0.1,
        "max_freshness_sec": _MAX_FRESHNESS_SEC,
        "stablecoins": sorted(_STABLECOINS),
        "blacklist": sorted(str(item).upper() for item in cfg.blacklist),
        "allowlist": sorted(str(item).upper() for item in getattr(cfg, "allowlist", None) or ()),
    }
    return HardGateDecisionV1(
        HARD_GATE_VERSION,
        candidate_key,
        evaluated_at,
        instrument,
        status,
        actionability,
        tuple(checks),
        tuple(sorted({item.reason_code for item in checks if item.status != "passed"})),
        canonical_fingerprint({"candidate_key": candidate_key, "instrument": instrument, "checks": [asdict(item) for item in checks]}),
        canonical_fingerprint(policy),
    )


__all__ = [
    "HARD_GATE_VERSION", "HardGateCheckV1", "HardGateDecisionV1",
    "canonical_fingerprint", "evaluate_hard_gate",
]
