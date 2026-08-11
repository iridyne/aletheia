"""Position X offline material parsing and compact structure helpers.

This module is strictly observation-only. It turns authorized offline research
materials into:

1. compact knowledge cards (safe summaries, never raw prompts/secrets), and
2. normalized perp-structure snapshots / advisory context blocks.

It does not call external APIs, does not persist secrets, and does not alter
trading/risk/execution behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel

from aletheia.data.models import PerpStructureSnapshot


_COMPOSITE_WEIGHTS = {
    "cgo": 0.95136998,
    "vap": 0.64689392,
    "skw": 0.44539054,
    "lfa": 1.28031713,
    "wp": 0.27750722,
}
_COMPOSITE_INTERCEPT = -0.06909579
_DIRECTIONAL_KEYS = ("cgo", "vap", "skw", "lfa", "wp")
_SENSITIVE_PATTERNS = (
    "api_key",
    "token",
    "credential",
    "balance",
    "order",
    "wallet",
    "address",
    "proxy",
    "secret",
)
_MATERIAL_FILES = {
    "signals": "sndk_intelligence_signals.json",
    "prompt_templates": "prompt_templates.json",
    "strategy_details": "strategy_details_with_prompts.json",
    "composite_fit": "composite_z_fit_data.json",
    "dimension_fit": "dim_internal_fit.json",
}
_MATERIAL_SUBDIRS = ("", "04_evidence", "vergex_extracted_data")
_ANALYSIS_SAMPLE_GLOB = "analysis_*.json"


class PositionXKnowledgeCard(BaseModel):
    card_type: str
    key: str
    title: str
    summary: str
    source_ref: str
    source_hash: str
    agent_template: str
    risk_caveat: str
    action_boundary: str


class PositionXMaterialBundle(BaseModel):
    materials: dict[str, Any]
    knowledge_cards: list[PositionXKnowledgeCard]
    snapshots: list[PerpStructureSnapshot]


def load_position_x_materials(material_dir: str | Path) -> dict[str, Any]:
    """Load only the safe, whitelisted offline Position X research files."""
    root = Path(material_dir)
    materials: dict[str, Any] = {}
    search_roots = _material_search_roots(root)
    for key, name in _MATERIAL_FILES.items():
        path = _find_material_file(search_roots, name)
        if path is None:
            continue
        materials[key] = json.loads(path.read_text(encoding="utf-8"))
    analysis_samples: list[dict[str, Any]] = []
    for search_root in search_roots:
        for path in sorted(search_root.glob(_ANALYSIS_SAMPLE_GLOB)):
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                analysis_samples.append(parsed)
    if analysis_samples:
        materials["analysis_samples"] = analysis_samples
    return materials


def extract_position_x_material_bundle(
    material_dir: str | Path,
    *,
    signal_payloads: list[dict[str, Any]] | None = None,
) -> PositionXMaterialBundle:
    """Read an authorized offline material directory and derive cards + snapshots."""
    materials = load_position_x_materials(material_dir)
    cards = extract_position_x_knowledge_cards(materials)
    snapshots: list[PerpStructureSnapshot] = []
    payloads: list[dict[str, Any]] = []
    signal_payload = materials.get("signals")
    if isinstance(signal_payload, dict):
        payloads.append(signal_payload)
    payloads.extend(signal_payloads or [])
    for payload in payloads:
        try:
            snapshots.append(extract_position_x_snapshot(payload))
        except (TypeError, ValueError):
            continue
    return PositionXMaterialBundle(materials=materials, knowledge_cards=cards, snapshots=snapshots)


def extract_position_x_knowledge_cards(materials: dict[str, Any]) -> list[PositionXKnowledgeCard]:
    """Build safe knowledge cards from authorized offline materials.

    Raw prompts, addresses, balances, credentials, tokens, and account payloads
    are intentionally ignored. Cards contain only derived summaries.
    """
    cards: list[PositionXKnowledgeCard] = []
    action_boundary = (
        "advisory_only: observation evidence only; never open, close, resize, or lever up from this card alone."
    )

    signal_payload = materials.get("signals") or {}
    signal_data = signal_payload.get("data") if isinstance(signal_payload, dict) else None
    if isinstance(signal_data, dict):
        structure_read = _safe_text(signal_data.get("structureRead"))
        for dim in signal_data.get("dimensions", []) or []:
            key = str(dim.get("key") or "").strip().lower()
            if not key:
                continue
            label = _safe_text(dim.get("label")) or key.upper()
            detail = _safe_text(dim.get("detail"))
            summary = _compact_join(
                _safe_text(dim.get("what")),
                detail,
            )
            agent_template = _compact_join(
                structure_read,
                f"{key} read: {detail}" if detail else "",
            )
            raw_source = {
                "key": key,
                "label": dim.get("label"),
                "what": dim.get("what"),
                "detail": dim.get("detail"),
            }
            cards.append(
                PositionXKnowledgeCard(
                    card_type="dimension_definition",
                    key=f"dimension:{key}",
                    title=label,
                    summary=summary,
                    source_ref=f"signals.dimensions.{key}",
                    source_hash=_stable_hash(raw_source),
                    agent_template=agent_template,
                    risk_caveat="Percentile/rank describe positioning state, not a price forecast.",
                    action_boundary=action_boundary,
                )
            )

        cards.append(_composite_fit_card(materials.get("composite_fit"), action_boundary))

    cards.extend(_dimension_fit_cards(materials.get("dimension_fit"), action_boundary))
    cards.extend(_analysis_pattern_cards(materials.get("analysis_samples"), action_boundary))

    templates = ((materials.get("prompt_templates") or {}).get("templates") or [])
    for idx, template in enumerate(templates[:2]):
        content = _safe_text(template.get("content"))
        if not content:
            continue
        cards.append(
            PositionXKnowledgeCard(
                card_type="agent_prompt_pattern",
                key=f"prompt_pattern:{idx}",
                title=_safe_text(template.get("name")) or "Prompt Pattern",
                summary=_summarize_prompt_pattern(content),
                source_ref=f"prompt_templates.templates[{idx}]",
                source_hash=_stable_hash(
                    {
                        "name": template.get("name"),
                        "prompt_type": template.get("prompt_type"),
                        "content": template.get("content"),
                    }
                ),
                agent_template="Compress external playbooks into a few evidence bullets; never inject raw partner prompts.",
                risk_caveat="Prompt structure can organize evidence, but it cannot bypass Aletheia's local decision chain.",
                action_boundary=action_boundary,
            )
        )

    strategy_details = materials.get("strategy_details") or []
    if isinstance(strategy_details, list):
        for idx, detail in enumerate(strategy_details[:2]):
            risk_control = detail.get("risk_control") or {}
            summary = _compact_join(
                f"Leverage cap {risk_control.get('max_leverage')}" if risk_control.get("max_leverage") is not None else "",
                "manual kill-switch only" if risk_control.get("kill_switch") else "",
                _safe_text(((detail.get("prompts") or {}).get("system"))),
            )
            cards.append(
                PositionXKnowledgeCard(
                    card_type="risk_boundary",
                    key=f"risk_boundary:{idx}",
                    title=_safe_text(detail.get("name")) or "Risk Boundary",
                    summary=summary or "External structure material must remain advisory-only.",
                    source_ref=f"strategy_details[{idx}]",
                    source_hash=_stable_hash(
                        {
                            "name": detail.get("name"),
                            "risk_control": risk_control,
                            "system": (detail.get("prompts") or {}).get("system"),
                        }
                    ),
                    agent_template="State the boundary explicitly: external structure evidence informs judgment but does not trigger action.",
                    risk_caveat="Do not inherit external leverage, account, or auto-execution conventions.",
                    action_boundary=action_boundary,
                )
            )

    return cards


def compute_position_x_features(
    holder_rows: list[dict[str, Any]],
    *,
    mark_price: float,
    liq_band_pct: float = 0.15,
    value_area_ratio: float = 0.70,
    price_bin_width_pct: float = 0.0025,
    min_wall_share: float = 0.01,
    total_market_oi_usd: float | None = None,
) -> dict[str, Any]:
    """Compute the frozen P0 offline position-structure benchmark."""
    if not math.isfinite(mark_price) or mark_price <= 0:
        raise ValueError("mark_price must be positive")
    if not 0 < liq_band_pct < 1:
        raise ValueError("liq_band_pct must be between 0 and 1")
    if not 0 < value_area_ratio <= 1:
        raise ValueError("value_area_ratio must be between 0 and 1")
    if not 0 < price_bin_width_pct < 1:
        raise ValueError("price_bin_width_pct must be between 0 and 1")
    if not 0 < min_wall_share <= 1:
        raise ValueError("min_wall_share must be between 0 and 1")
    if total_market_oi_usd is not None and (not math.isfinite(total_market_oi_usd) or total_market_oi_usd <= 0):
        raise ValueError("total_market_oi_usd must be positive when provided")

    long_rows = [r for r in holder_rows if str(r.get("side") or "").lower() == "long"]
    short_rows = [r for r in holder_rows if str(r.get("side") or "").lower() == "short"]
    band_low = mark_price * (1 - liq_band_pct)
    band_high = mark_price * (1 + liq_band_pct)

    notionals = [n for row in holder_rows if (n := _positive_number(row.get("current_notional"))) is not None]
    total_notional = sum(notionals)
    cost_bins: dict[int, float] = {}
    support_bins: dict[int, float] = {}
    resistance_bins: dict[int, float] = {}
    for row in holder_rows:
        entry = _positive_number(row.get("entry_price"))
        notional = _positive_number(row.get("current_notional"))
        if entry is None or notional is None:
            continue
        key = _price_bin(entry, mark_price, price_bin_width_pct)
        cost_bins[key] = cost_bins.get(key, 0.0) + notional
        side = str(row.get("side") or "").lower()
        if side == "short" and entry < mark_price:
            support_bins[key] = support_bins.get(key, 0.0) + notional
        elif side == "long" and entry > mark_price:
            resistance_bins[key] = resistance_bins.get(key, 0.0) + notional

    poc_key, value_area_keys = _cost_basis_value_area(cost_bins, value_area_ratio)
    poc = _price_bin_center(poc_key, mark_price, price_bin_width_pct) if poc_key is not None else None
    value_area_low = (
        _price_bin_center(min(value_area_keys), mark_price, price_bin_width_pct) if value_area_keys else None
    )
    value_area_high = (
        _price_bin_center(max(value_area_keys), mark_price, price_bin_width_pct) if value_area_keys else None
    )
    value_area_covered_ratio = (
        sum(cost_bins.get(key, 0.0) for key in value_area_keys) / sum(cost_bins.values()) if value_area_keys else None
    )

    support_walls = _qualified_walls(support_bins, total_notional, mark_price, price_bin_width_pct, min_wall_share)
    resistance_walls = _qualified_walls(
        resistance_bins, total_notional, mark_price, price_bin_width_pct, min_wall_share
    )
    support_wall = min(support_walls, key=lambda wall: abs(mark_price - wall[0]), default=None)
    resistance_wall = min(resistance_walls, key=lambda wall: abs(mark_price - wall[0]), default=None)
    support = support_wall[0] if support_wall else None
    resistance = resistance_wall[0] if resistance_wall else None
    support_notional = support_wall[1] if support_wall else 0.0
    resistance_notional = resistance_wall[1] if resistance_wall else 0.0
    support_distance = (mark_price - support) / mark_price if support is not None else None
    resistance_distance = (resistance - mark_price) / mark_price if resistance is not None else None
    support_share = support_notional / total_notional if support_wall and total_notional > 0 else None
    resistance_share = resistance_notional / total_notional if resistance_wall and total_notional > 0 else None
    support_strength = (
        support_share / max(support_distance, price_bin_width_pct)
        if support_share is not None and support_distance is not None
        else None
    )
    resistance_strength = (
        resistance_share / max(resistance_distance, price_bin_width_pct)
        if resistance_share is not None and resistance_distance is not None
        else None
    )
    skw_raw = (
        math.log((resistance_distance + 1e-9) / (support_distance + 1e-9))
        if support_distance is not None and resistance_distance is not None
        else None
    )
    wp_raw = (
        math.log((support_notional + 1.0) / (resistance_notional + 1.0)) if support_wall and resistance_wall else None
    )
    vap_raw = (
        (mark_price - value_area_low) / (value_area_high - value_area_low)
        if value_area_low is not None and value_area_high is not None and value_area_high > value_area_low
        else None
    )

    valid_liq_rows: list[tuple[str, float, float]] = []
    liq_bins: dict[int, float] = {}
    near_liq_notional = 0.0
    for row in holder_rows:
        liq = _positive_number(row.get("liquidation_price"))
        notional = _positive_number(row.get("current_notional"))
        side = str(row.get("side") or "").lower()
        if liq is None or notional is None or side not in {"long", "short"}:
            continue
        valid_liq_rows.append((side, liq, notional))
        if band_low <= liq <= band_high:
            key = _price_bin(liq, mark_price, price_bin_width_pct)
            liq_bins[key] = liq_bins.get(key, 0.0) + notional
        if abs(liq - mark_price) / mark_price <= 0.05:
            near_liq_notional += notional

    short_liq_above = (
        sum(notional for side, liq, notional in valid_liq_rows if side == "short" and mark_price < liq <= band_high)
        if valid_liq_rows
        else None
    )
    long_liq_below = (
        sum(notional for side, liq, notional in valid_liq_rows if side == "long" and band_low <= liq < mark_price)
        if valid_liq_rows
        else None
    )
    in_band_liq = sum(liq_bins.values())
    convexity = max(liq_bins.values()) / in_band_liq if in_band_liq > 0 else (0.0 if valid_liq_rows else None)
    sample_cascade = near_liq_notional / total_notional if valid_liq_rows and total_notional > 0 else None
    market_cascade = (
        near_liq_notional / total_market_oi_usd
        if total_market_oi_usd and total_market_oi_usd >= near_liq_notional
        else None
    )
    market_cascade_status = (
        "available" if market_cascade is not None
        else "invalid_denominator" if total_market_oi_usd is not None
        else "unavailable"
    )

    long_losses = [
        abs(pnl)
        for row in long_rows
        if (pnl := _number(row.get("unrealized_pnl"))) is not None
        and math.isfinite(pnl)
        and pnl < 0
    ]
    short_losses = [
        abs(pnl)
        for row in short_rows
        if (pnl := _number(row.get("unrealized_pnl"))) is not None
        and math.isfinite(pnl)
        and pnl < 0
    ]
    long_overhang_loss = sum(long_losses)
    short_overhang_loss = sum(short_losses)
    cgo_available = bool(long_losses or short_losses)
    cgo_raw = math.log((short_overhang_loss + 1.0) / (long_overhang_loss + 1.0)) if cgo_available else None
    cgo_direction = _direction_from_balance(short_overhang_loss, long_overhang_loss) if cgo_available else None
    vap_direction = _direction_from_vap(mark_price, value_area_low, value_area_high, poc)
    skw_direction = _direction_from_proximity(mark_price, support, resistance)
    lfa_direction = _direction_from_balance(short_liq_above, long_liq_below) if valid_liq_rows else None
    lfa_raw = (
        math.log((short_liq_above + 1.0) / (long_liq_below + 1.0))
        if short_liq_above is not None and long_liq_below is not None
        else None
    )
    wp_direction = (
        _direction_from_balance(support_notional, resistance_notional) if support_wall or resistance_wall else None
    )

    ranked_notionals = sorted(notionals, reverse=True)
    top10_conc = sum(ranked_notionals[:10]) / total_notional if total_notional > 0 else None
    top50_conc = sum(ranked_notionals[:50]) / total_notional if total_notional > 0 else None
    hhi = sum((notional / total_notional) ** 2 for notional in ranked_notionals) if total_notional > 0 else None

    return {
        "short_liq_above_usd": round(short_liq_above, 2) if short_liq_above is not None else None,
        "long_liq_below_usd": round(long_liq_below, 2) if long_liq_below is not None else None,
        "long_overhang_pnl_usd": round(-long_overhang_loss, 2) if cgo_available else None,
        "short_overhang_pnl_usd": round(-short_overhang_loss, 2) if cgo_available else None,
        "cgo_raw": round(cgo_raw, 6) if cgo_raw is not None else None,
        "lfa_raw": round(lfa_raw, 6) if lfa_raw is not None else None,
        "support": support,
        "resistance": resistance,
        "support_wall_notional_usd": round(support_notional, 2) if support_wall else None,
        "resistance_wall_notional_usd": round(resistance_notional, 2) if resistance_wall else None,
        "support_wall_share": round(support_share, 6) if support_share is not None else None,
        "resistance_wall_share": round(resistance_share, 6) if resistance_share is not None else None,
        "support_wall_distance_pct": round(support_distance, 6) if support_distance is not None else None,
        "resistance_wall_distance_pct": round(resistance_distance, 6) if resistance_distance is not None else None,
        "support_wall_strength": round(support_strength, 6) if support_strength is not None else None,
        "resistance_wall_strength": round(resistance_strength, 6) if resistance_strength is not None else None,
        "skw_raw": round(skw_raw, 6) if skw_raw is not None else None,
        "wp_raw": round(wp_raw, 6) if wp_raw is not None else None,
        "vap_raw": round(vap_raw, 6) if vap_raw is not None else None,
        "poc": poc,
        "value_area_low": value_area_low,
        "value_area_high": value_area_high,
        "cost_profile_bin_count": len(cost_bins),
        "value_area_bin_count": len(value_area_keys),
        "value_area_covered_notional_ratio": (
            round(value_area_covered_ratio, 6) if value_area_covered_ratio is not None else None
        ),
        "support_wall_count": len(support_walls),
        "resistance_wall_count": len(resistance_walls),
        "top10_concentration": round(top10_conc, 6) if top10_conc is not None else None,
        "top50_concentration": round(top50_conc, 6) if top50_conc is not None else None,
        "holder_hhi": round(hhi, 6) if hhi is not None else None,
        "effective_holder_count": round(1.0 / hhi, 6) if hhi else None,
        "convexity": round(convexity, 6) if convexity is not None else None,
        "cascade_vuln_pct": round(market_cascade, 6) if market_cascade is not None else None,
        "sample_cascade_vuln_pct": round(sample_cascade, 6) if sample_cascade is not None else None,
        "cgo_direction": cgo_direction,
        "vap_direction": vap_direction,
        "skw_direction": skw_direction,
        "lfa_direction": lfa_direction,
        "wp_direction": wp_direction,
        "holder_count": len(holder_rows),
        "covered_holder_count": len(notionals),
        "long_holder_count": len(long_rows),
        "short_holder_count": len(short_rows),
        "availability": {
            "cost_profile": "available" if cost_bins else "unavailable",
            "holder_concentration": "available" if ranked_notionals else "unavailable",
            "cost_walls": "available" if support_wall or resistance_wall else "unavailable",
            "cgo": "available" if cgo_available else "unavailable",
            "liquidation_structure": "available" if valid_liq_rows else "unavailable",
            "market_cascade": market_cascade_status,
        },
        "parameters": {
            "price_bin_width_pct": price_bin_width_pct,
            "liq_band_pct": liq_band_pct,
            "value_area_ratio": value_area_ratio,
            "min_wall_share": min_wall_share,
            "cgo_epsilon_usd": 1.0,
        },
    }


def _price_bin(price: float, reference_price: float, width_pct: float) -> int:
    return round(math.log(price / reference_price) / math.log1p(width_pct))


def _price_bin_center(key: int, reference_price: float, width_pct: float) -> float:
    return reference_price * (1 + width_pct) ** key


def _cost_basis_value_area(
    bins: dict[int, float],
    ratio: float,
) -> tuple[int | None, set[int]]:
    if not bins:
        return None, set()
    poc = max(bins, key=lambda key: (bins[key], -abs(key), -key))
    selected = {poc}
    running = bins[poc]
    target = sum(bins.values()) * ratio
    left = right = poc
    lower = min(bins)
    upper = max(bins)
    while running < target and (left > lower or right < upper):
        left_weight = bins.get(left - 1, 0.0) if left > lower else -1.0
        right_weight = bins.get(right + 1, 0.0) if right < upper else -1.0
        if left_weight >= right_weight:
            left -= 1
            selected.add(left)
            running += max(left_weight, 0.0)
        else:
            right += 1
            selected.add(right)
            running += max(right_weight, 0.0)
    return poc, selected


def _qualified_walls(
    bins: dict[int, float],
    total_notional: float,
    reference_price: float,
    width_pct: float,
    min_share: float,
) -> list[tuple[float, float]]:
    qualified = [key for key, weight in bins.items() if total_notional > 0 and weight / total_notional >= min_share]
    if not qualified:
        return []
    clusters: list[list[int]] = []
    for key in sorted(qualified):
        if not clusters or key > clusters[-1][-1] + 1:
            clusters.append([key])
        else:
            clusters[-1].append(key)
    walls: list[tuple[float, float]] = []
    for cluster in clusters:
        weight = sum(bins[key] for key in cluster)
        centroid = sum(_price_bin_center(key, reference_price, width_pct) * bins[key] for key in cluster) / weight
        walls.append((centroid, weight))
    return walls


def compute_position_x_composite_z(dimension_percentiles: dict[str, float | int | None]) -> float:
    """Research prior v1 composite score from centered directional percentiles."""
    total = _COMPOSITE_INTERCEPT
    for key, weight in _COMPOSITE_WEIGHTS.items():
        raw = dimension_percentiles.get(key)
        if raw is None:
            continue
        total += weight * ((float(raw) - 50.0) / 100.0)
    return round(total, 2)


def compute_cross_sectional_percentiles(values: dict[str, float | int | None]) -> dict[str, float | None]:
    """Return 0..100 percentile ranks using average ranks for ties."""
    valid = [
        (key, float(value))
        for key, value in values.items()
        if value is not None and math.isfinite(float(value))
    ]
    if not valid:
        return {key: None for key in values}
    ordered = sorted(valid, key=lambda item: item[1])
    denom = max(len(ordered) - 1, 1)
    out: dict[str, float | None] = {key: None for key in values}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        percentile = 50.0 if len(ordered) == 1 else ((start + end - 1) / 2) / denom * 100.0
        for key, _ in ordered[start:end]:
            out[key] = round(percentile, 4)
        start = end
    return out


def compute_position_x_batch(
    markets: list[dict[str, Any]],
    *,
    min_markets: int = 3,
    min_coverage_ratio: float = 0.80,
    max_source_skew_seconds: int = 300,
    liq_band_pct: float = 0.15,
    value_area_ratio: float = 0.70,
    price_bin_width_pct: float = 0.0025,
    min_wall_share: float = 0.01,
) -> dict[str, Any]:
    """Build an observation-only cross-sectional benchmark from sanitized holders."""
    if not isinstance(markets, list):
        raise ValueError("markets must be a list")
    if min_markets < 1:
        raise ValueError("min_markets must be positive")
    if len(markets) < min_markets:
        raise ValueError(f"markets must contain at least {min_markets} rows")
    if not 0 < min_coverage_ratio <= 1:
        raise ValueError("min_coverage_ratio must be between 0 and 1")
    if max_source_skew_seconds < 0:
        raise ValueError("max_source_skew_seconds must be non-negative")

    prepared: dict[str, dict[str, Any]] = {}
    timestamps: list[datetime] = []
    for market in markets:
        if not isinstance(market, dict):
            raise ValueError("each market must be an object")
        symbol = str(market.get("symbol") or "").strip()
        venue = str(market.get("venue") or "hyperliquid").strip()
        market_type = str(market.get("market_type") or "perp").strip().lower()
        key = f"{venue}|{market_type}|{symbol}"
        if not symbol or market_type != "perp":
            raise ValueError("each market requires a symbol and market_type=perp")
        if key in prepared:
            raise ValueError(f"duplicate market: {key}")
        coverage = _number(market.get("coverage_ratio"))
        if coverage is None or not math.isfinite(coverage) or not min_coverage_ratio <= coverage <= 1:
            raise ValueError(f"coverage_ratio below {min_coverage_ratio}: {key}")
        as_of_time = str(market.get("as_of_time") or "").strip()
        try:
            parsed_time = datetime.fromisoformat(as_of_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid as_of_time: {key}") from exc
        if parsed_time.tzinfo is None:
            parsed_time = parsed_time.replace(tzinfo=timezone.utc)
        timestamps.append(parsed_time.astimezone(timezone.utc))
        holders = market.get("holders")
        if not isinstance(holders, list) or not holders or not all(isinstance(row, dict) for row in holders):
            raise ValueError(f"holders must be a non-empty list: {key}")
        mark_price = _positive_number(market.get("mark_price"))
        total_oi = _positive_number(market.get("total_market_oi_usd"))
        if mark_price is None or total_oi is None:
            raise ValueError(f"mark_price and total_market_oi_usd must be positive: {key}")
        prepared[key] = {
            "market": market,
            "features": compute_position_x_features(
                holders,
                mark_price=mark_price,
                total_market_oi_usd=total_oi,
                liq_band_pct=liq_band_pct,
                value_area_ratio=value_area_ratio,
                price_bin_width_pct=price_bin_width_pct,
                min_wall_share=min_wall_share,
            ),
            "coverage_ratio": coverage,
            "mark_price": mark_price,
            "as_of_time": parsed_time.astimezone(timezone.utc).isoformat(),
        }

    if (max(timestamps) - min(timestamps)).total_seconds() > max_source_skew_seconds:
        raise ValueError("market as_of_time values exceed max_source_skew_seconds")

    percentiles = {
        dimension: compute_cross_sectional_percentiles({
            key: item["features"].get(f"{dimension}_raw") for key, item in prepared.items()
        })
        for dimension in _DIRECTIONAL_KEYS
    }
    scores: dict[str, tuple[float, str]] = {}
    rows: list[dict[str, Any]] = []
    for key, item in prepared.items():
        features = item["features"]
        dimensions = {
            dimension: {
                "raw": features.get(f"{dimension}_raw"),
                "percentile": percentiles[dimension][key],
                "direction": features.get(f"{dimension}_direction"),
            }
            for dimension in _DIRECTIONAL_KEYS
        }
        valid_percentiles = [
            value["percentile"] for value in dimensions.values() if value["percentile"] is not None
        ]
        dimension_percentiles = {name: value["percentile"] for name, value in dimensions.items()}
        availability_stratum = "+".join(
            name for name, value in dimensions.items() if value["percentile"] is not None
        ) or "none"
        composite = compute_position_x_composite_z(dimension_percentiles) if valid_percentiles else None
        equal_weight = round(
            sum((value - 50.0) / 100.0 for value in valid_percentiles) / len(valid_percentiles), 4
        ) if valid_percentiles else 0.0
        if composite is not None:
            scores[key] = (composite, availability_stratum)
        market = item["market"]
        risk_context = {
            "benchmark_version": "position-x-p1-v1",
            "coverage_ratio": item["coverage_ratio"],
            "holder_hhi": features["holder_hhi"],
            "effective_holder_count": features["effective_holder_count"],
            "convexity": features["convexity"],
            "market_cascade_vuln_pct": features["cascade_vuln_pct"],
            "sample_cascade_vuln_pct": features["sample_cascade_vuln_pct"],
            "value_area_covered_notional_ratio": features["value_area_covered_notional_ratio"],
            "equal_weight_score": equal_weight,
            "no_structure_score": 0.0,
            "availability_stratum": availability_stratum,
            "availability": features["availability"],
            "parameters": features["parameters"],
        }
        rows.append({
            "symbol": market["symbol"],
            "venue": str(market.get("venue") or "hyperliquid"),
            "as_of_time": item["as_of_time"],
            "reference_price": item["mark_price"],
            "data_quality": "complete" if len(valid_percentiles) == len(_DIRECTIONAL_KEYS) else "partial",
            "coverage_note": f"sanitized holder coverage={item['coverage_ratio']:.4f}",
            "poc": features["poc"],
            "value_area_low": features["value_area_low"],
            "value_area_high": features["value_area_high"],
            "support": features["support"],
            "resistance": features["resistance"],
            "short_liq_above_usd": features["short_liq_above_usd"],
            "long_liq_below_usd": features["long_liq_below_usd"],
            "long_unrealized_pnl_usd": features["long_overhang_pnl_usd"],
            "short_unrealized_pnl_usd": features["short_overhang_pnl_usd"],
            "top10_concentration": features["top10_concentration"],
            "top50_concentration": features["top50_concentration"],
            "holder_count": features["holder_count"],
            "long_holder_count": features["long_holder_count"],
            "short_holder_count": features["short_holder_count"],
            "cgo_direction": features["cgo_direction"],
            "vap_direction": features["vap_direction"],
            "lfa_direction": features["lfa_direction"],
            "cascade_risk": features["cascade_vuln_pct"],
            "convexity": features["convexity"],
            "structure_bias": (
                "bullish" if composite is not None and composite > 0
                else "bearish" if composite is not None and composite < 0
                else "neutral" if composite is not None
                else None
            ),
            "composite_z": composite,
            "universe_size": len(prepared),
            "dimension_scores_json": json.dumps(dimensions, sort_keys=True, separators=(",", ":")),
            "risk_context_json": json.dumps(risk_context, sort_keys=True, separators=(",", ":")),
        })

    parameters = {
        "min_markets": min_markets,
        "min_coverage_ratio": min_coverage_ratio,
        "max_source_skew_seconds": max_source_skew_seconds,
        "liq_band_pct": liq_band_pct,
        "value_area_ratio": value_area_ratio,
        "price_bin_width_pct": price_bin_width_pct,
        "min_wall_share": min_wall_share,
    }
    canonical = json.dumps(
        {
            "markets": sorted(
                markets,
                key=lambda market: (
                    str(market.get("venue") or "hyperliquid"),
                    str(market.get("market_type") or "perp"),
                    str(market.get("symbol") or ""),
                ),
            ),
            "parameters": parameters,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    batch_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    for row in rows:
        key = f"{row['venue']}|perp|{row['symbol']}"
        context = json.loads(row["risk_context_json"])
        if key in scores:
            score, stratum = scores[key]
            peers = [peer_score for peer_score, peer_stratum in scores.values() if peer_stratum == stratum]
            row["structure_rank"] = 1 + sum(peer_score > score for peer_score in peers)
            context["rank_universe_size"] = len(peers)
        else:
            row["structure_rank"] = None
            context["rank_universe_size"] = 0
        context["batch_id"] = batch_id
        row["risk_context_json"] = json.dumps(context, sort_keys=True, separators=(",", ":"))
    rows.sort(key=lambda row: (row["structure_rank"] is None, row["structure_rank"] or 0, row["venue"], row["symbol"]))
    return {
        "batch_id": batch_id,
        "universe_size": len(rows),
        "mean_coverage_ratio": round(sum(item["coverage_ratio"] for item in prepared.values()) / len(rows), 6),
        "parameters": parameters,
        "rows": rows,
    }


def extract_position_x_snapshot(payload: dict[str, Any]) -> PerpStructureSnapshot:
    """Normalize a Position X signal payload into a PerpStructureSnapshot."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise ValueError("payload must be a dict with Position X signal data")

    symbol = str(payload.get("symbol") or data.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("Position X payload requires a normalized symbol")

    dimensions = {str(d.get("key") or "").lower(): d for d in data.get("dimensions", []) or []}
    levels = data.get("levels") or {}
    metrics = data.get("metrics") or {}
    now = datetime.now(timezone.utc).isoformat()

    dimension_scores = {
        key: {
            "direction": _safe_text(dim.get("direction")),
            "strength": _safe_text(dim.get("strength")),
            "percentile": dim.get("percentile"),
            "detail": _safe_text(dim.get("detail")),
        }
        for key, dim in dimensions.items()
        if key
    }
    risk_context = {
        key: {"detail": _safe_text(dimensions[key].get("detail"))}
        for key in ("convexity", "cascade")
        if key in dimensions
    }
    if data.get("structureRead"):
        risk_context["summary"] = {"detail": _safe_text(data.get("structureRead"))}

    normalized = {
        "symbol": symbol,
        "venue": "hyperliquid",
        "source": str(payload.get("source") or "position_x"),
        "as_of_time": str(payload.get("as_of_time") or data.get("as_of_time") or now),
        "observed_at": str(payload.get("observed_at") or now),
        "data_quality": str(metrics.get("state") or data.get("data_quality") or "unknown"),
        "coverage_note": _safe_text((payload.get("meta") or {}).get("coverage")) if isinstance(payload.get("meta"), dict) else None,
        "poc": _number(levels.get("poc")),
        "value_area_low": _number(levels.get("valueAreaLow") or levels.get("value_area_low")),
        "value_area_high": _number(levels.get("valueAreaHigh") or levels.get("value_area_high")),
        "support": _number(levels.get("support")),
        "resistance": _number(levels.get("resistance")),
        "magnet": _number(levels.get("magnet")),
        "short_liq_above_usd": _number(metrics.get("shortLiqAbove")),
        "long_liq_below_usd": _number(metrics.get("longLiqBelow")),
        "long_unrealized_pnl_usd": _number(metrics.get("longOverhangPnl")),
        "short_unrealized_pnl_usd": _number(metrics.get("shortOverhangPnl")),
        "top10_concentration": _number(metrics.get("top10Pct")),
        "top50_concentration": _number(metrics.get("top50Pct")),
        "holder_count": _int(metrics.get("includedPositions")),
        "long_holder_count": _int(metrics.get("holders_long") or metrics.get("longHolderCount")),
        "short_holder_count": _int(metrics.get("holders_short") or metrics.get("shortHolderCount")),
        "cgo_direction": _safe_text((dimensions.get("cgo") or {}).get("direction")),
        "vap_direction": _safe_text((dimensions.get("vap") or {}).get("direction")),
        "lfa_direction": _safe_text((dimensions.get("lfa") or {}).get("direction")),
        "cascade_risk": _number(metrics.get("cascadeVulnPct")),
        "convexity": _number(metrics.get("convexity")),
        "vendor_bias": _safe_text(data.get("bias")),
        "vendor_rank": _int(data.get("rank")),
        "vendor_universe_size": _int(data.get("universeSize")),
        "structure_bias": _safe_text(data.get("bias")),
        "composite_z": _number(data.get("compositeZ") or data.get("composite_z")),
        "structure_rank": _int(data.get("rank")),
        "universe_size": _int(data.get("universeSize") or data.get("universe_size")),
        "dimension_scores_json": json.dumps(dimension_scores, sort_keys=True, separators=(",", ":")),
        "risk_context_json": json.dumps(risk_context, sort_keys=True, separators=(",", ":")),
    }
    normalized["raw_json"] = json.dumps(
        {
            "symbol": normalized["symbol"],
            "levels": {
                "poc": normalized["poc"],
                "support": normalized["support"],
                "resistance": normalized["resistance"],
                "value_area_low": normalized["value_area_low"],
                "value_area_high": normalized["value_area_high"],
            },
            "metrics": {
                "short_liq_above_usd": normalized["short_liq_above_usd"],
                "long_liq_below_usd": normalized["long_liq_below_usd"],
                "long_unrealized_pnl_usd": normalized["long_unrealized_pnl_usd"],
                "short_unrealized_pnl_usd": normalized["short_unrealized_pnl_usd"],
                "top10_concentration": normalized["top10_concentration"],
                "convexity": normalized["convexity"],
                "cascade_risk": normalized["cascade_risk"],
            },
            "structure_bias": normalized["structure_bias"],
            "composite_z": normalized["composite_z"],
            "structure_rank": normalized["structure_rank"],
            "universe_size": normalized["universe_size"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PerpStructureSnapshot(**normalized)


def build_position_x_agent_context(
    row: dict[str, Any] | None,
    *,
    now_iso: str | None = None,
    max_age_minutes: int = 60,
) -> dict[str, Any]:
    """Build a compact advisory-only agent block from a stored structure row."""
    base = {
        "status": "insufficient_data",
        "advisory_only": True,
        "bias": None,
        "composite_z": None,
        "rank": None,
        "universe_size": None,
        "dimension_reads": {},
        "risk_context": {},
        "caveats": [],
        "source_identity": None,
        "rendered": "Position X (advisory only): insufficient_data",
    }
    if not row:
        return base

    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    as_of = _parse_iso(row.get("as_of_time")) or _parse_iso(row.get("observed_at"))
    if not as_of or now - as_of > timedelta(minutes=max_age_minutes):
        return base

    dimension_reads = _loads_json(row.get("dimension_scores_json"))
    risk_context = _loads_json(row.get("risk_context_json"))
    caveats: list[str] = []
    if str(row.get("data_quality") or "").lower() not in ("", "complete"):
        caveats.append("degraded_data")

    bias = row.get("structure_bias") or row.get("vendor_bias")
    composite = row.get("composite_z")
    rank = row.get("structure_rank") or row.get("vendor_rank")
    universe_size = row.get("universe_size") or row.get("vendor_universe_size")

    rendered_parts = ["Position X (advisory only)"]
    if bias:
        rendered_parts.append(f"bias {bias}")
    if composite is not None:
        rendered_parts.append(f"composite_z {float(composite):+.2f}".replace("+", ""))
    if rank is not None and universe_size:
        rendered_parts.append(f"rank {int(rank)}/{int(universe_size)}")
    dim_bits: list[str] = []
    for key in _DIRECTIONAL_KEYS:
        dim = dimension_reads.get(key) or {}
        direction = dim.get("direction")
        strength = dim.get("strength")
        percentile = dim.get("percentile")
        if not direction:
            continue
        bit = f"{key} {direction}"
        if strength:
            bit += f" {strength}"
        if percentile is not None:
            bit += f" p{float(percentile):.1f}"
        dim_bits.append(bit)
    if dim_bits:
        rendered_parts.append("; ".join(dim_bits[:3]))
    if caveats:
        rendered_parts.append("caveat " + ",".join(caveats))

    return {
        "status": "ok",
        "advisory_only": True,
        "bias": bias,
        "composite_z": composite,
        "rank": rank,
        "universe_size": universe_size,
        "dimension_reads": dimension_reads,
        "risk_context": risk_context,
        "caveats": caveats,
        "source_identity": {
            "id": row.get("id"),
            "source": row.get("source"),
            "source_ref": row.get("source_ref"),
            "source_hash": row.get("source_hash"),
            "as_of_time": row.get("as_of_time"),
            "observed_at": row.get("observed_at"),
        },
        "rendered": ": ".join([rendered_parts[0], "; ".join(rendered_parts[1:])]) if len(rendered_parts) > 1 else rendered_parts[0],
    }


def _safe_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(pattern in lowered for pattern in _SENSITIVE_PATTERNS):
        return ""
    text = re.sub(r"\s+", " ", text)
    return text[:400]


def _material_search_roots(root: Path) -> list[Path]:
    search_roots: list[Path] = []
    for subdir in _MATERIAL_SUBDIRS:
        path = root / subdir if subdir else root
        if path.exists() and path.is_dir():
            search_roots.append(path)
    return search_roots


def _find_material_file(search_roots: list[Path], name: str) -> Path | None:
    for search_root in search_roots:
        candidate = search_root / name
        if candidate.exists():
            return candidate
    return None


def _compact_join(*parts: str) -> str:
    return " ".join(part for part in parts if part).strip()


def _summarize_prompt_pattern(content: str) -> str:
    lowered = content.lower()
    sentences: list[str] = []
    if "4h" in lowered or "4h " in lowered:
        sentences.append("Use higher timeframe trend context before lower timeframe execution.")
    if "15m" in lowered:
        sentences.append("Use a shorter execution window for entries once context aligns.")
    if "pullback" in lowered:
        sentences.append("Prefer pullback entries over chasing extremes in trending conditions.")
    if "range" in lowered or "ranging" in lowered:
        sentences.append("Treat range trades as mean-reversion setups around edges, not in the middle.")
    if not sentences:
        sentences.append("Compress the external template into evidence-ordering guidance, not a direct trading script.")
    return " ".join(sentences[:3])


def _composite_fit_card(composite_fit: Any, action_boundary: str) -> PositionXKnowledgeCard:
    sample_count = len(composite_fit) if isinstance(composite_fit, list) else 0
    summary = (
        f"Research prior v1 composite uses centered directional percentiles across "
        f"{sample_count or 'the sampled'} markets, with LFA/CGO carrying the heaviest weight."
    )
    return PositionXKnowledgeCard(
        card_type="formula_hint",
        key="formula:composite_research_prior_v1",
        title="Composite Score Prior",
        summary=summary,
        source_ref="composite_fit.research_prior_v1",
        source_hash=_stable_hash(
            {
                "weights": _COMPOSITE_WEIGHTS,
                "intercept": _COMPOSITE_INTERCEPT,
                "fit_rows": composite_fit if isinstance(composite_fit, list) else [],
            }
        ),
        agent_template="Use composite_z as a ranking clue only after reading the underlying dimension evidence.",
        risk_caveat="Composite score is a research prior; do not use it as a standalone trade switch.",
        action_boundary=action_boundary,
    )


def _dimension_fit_cards(dimension_fit: Any, action_boundary: str) -> list[PositionXKnowledgeCard]:
    if not isinstance(dimension_fit, list):
        return []
    cards: list[PositionXKnowledgeCard] = []
    lfa_hint = _best_rank_hint(
        dimension_fit,
        percentile_key="lfa",
        candidates={
            "squeeze_minus_flush_balance": lambda row: (
                _metric_value(row, "shortLiqAbove") - _metric_value(row, "longLiqBelow")
            )
            / (_metric_value(row, "shortLiqAbove") + _metric_value(row, "longLiqBelow") + 1.0),
        },
    )
    if lfa_hint is not None:
        cards.append(
            PositionXKnowledgeCard(
                card_type="formula_hint",
                key="formula:lfa_cross_sectional_rank",
                title="LFA Percentile Hint",
                summary=(
                    f"LFA percentile most closely tracks the cross-sectional rank of {lfa_hint['name']} "
                    f"(correlation {lfa_hint['correlation']:+.2f}) in the research sample."
                ),
                source_ref="dimension_fit.lfa",
                source_hash=_stable_hash({"rows": dimension_fit, "hint": lfa_hint}),
                agent_template="Treat LFA as fuel imbalance first, then compare it cross-sectionally against the rest of the universe.",
                risk_caveat="Fuel balance says where forced flow sits, not whether price must travel there next.",
                action_boundary=action_boundary,
            )
        )
    cgo_hint = _best_rank_hint(
        dimension_fit,
        percentile_key="cgo",
        candidates={
            "trapped_short_minus_long_overhang": lambda row: _metric_value(row, "gShort") - _metric_value(row, "gLong"),
            "trapped_long_minus_short_overhang": lambda row: _metric_value(row, "gLong") - _metric_value(row, "gShort"),
        },
    )
    if cgo_hint is not None:
        cards.append(
            PositionXKnowledgeCard(
                card_type="formula_hint",
                key="formula:cgo_cross_sectional_rank",
                title="CGO Percentile Hint",
                summary=(
                    f"CGO percentile most closely tracks the cross-sectional rank of {cgo_hint['name']} "
                    f"(correlation {cgo_hint['correlation']:+.2f}) in the research sample."
                ),
                source_ref="dimension_fit.cgo",
                source_hash=_stable_hash({"rows": dimension_fit, "hint": cgo_hint}),
                agent_template="Read CGO as where trapped supply or trapped support dominates before collapsing it into one label.",
                risk_caveat="CGO captures overhang balance, not timing precision.",
                action_boundary=action_boundary,
            )
        )
    context_only = any(
        isinstance(row, dict)
        and isinstance(row.get("pct"), dict)
        and (
            row["pct"].get("convexity") is None
            or row["pct"].get("cascade") is None
        )
        for row in dimension_fit
    )
    if context_only:
        cards.append(
            PositionXKnowledgeCard(
                card_type="formula_hint",
                key="formula:neutral_risk_context",
                title="Neutral Risk Context",
                summary="Convexity and cascade remain context-only in the sampled fit set, so they should stay as risk modifiers rather than directional triggers.",
                source_ref="dimension_fit.context_only",
                source_hash=_stable_hash({"rows": dimension_fit, "keys": ["convexity", "cascade"]}),
                agent_template="Use convexity and cascade to widen or narrow caution, not to override the directional dimensions.",
                risk_caveat="A concentrated liquidation map can amplify a move, but it does not define its sign by itself.",
                action_boundary=action_boundary,
            )
        )
    return cards


def _analysis_pattern_cards(analysis_samples: Any, action_boundary: str) -> list[PositionXKnowledgeCard]:
    if not isinstance(analysis_samples, list):
        return []
    cards: list[PositionXKnowledgeCard] = []
    for sample in analysis_samples[:4]:
        if not isinstance(sample, dict):
            continue
        symbol = _safe_text(sample.get("symbol") or "").strip()
        if not symbol:
            continue
        structure = sample.get("structure") if isinstance(sample.get("structure"), dict) else {}
        positioning = structure.get("positioning") if isinstance(structure.get("positioning"), dict) else {}
        whale = structure.get("whale") if isinstance(structure.get("whale"), dict) else {}
        top10 = _pct_text(positioning.get("top10"))
        top50 = _pct_text(positioning.get("top50"))
        summary = _compact_join(
            f"Example output keeps action {str(sample.get('action') or '').upper()} separate from the structure evidence.",
            f"Structure bias {_safe_text(structure.get('bias'))} rank {structure.get('rank')}/{structure.get('universe_size') or structure.get('universeSize')}"
            if structure.get("bias") or structure.get("rank") is not None
            else "",
            f"Positioning top10 {top10}, top50 {top50}." if top10 or top50 else "",
            f"Whale net {_safe_text(whale.get('net'))}." if whale.get("net") else "",
        )
        safe_source = {
            "symbol": sample.get("symbol"),
            "action": sample.get("action"),
            "reasoning": _safe_text(sample.get("reasoning")),
            "structure": {
                "bias": structure.get("bias"),
                "rank": structure.get("rank"),
                "universe_size": structure.get("universe_size") or structure.get("universeSize"),
                "levels": {
                    "poc": ((structure.get("levels") or {}).get("poc")) if isinstance(structure.get("levels"), dict) else None,
                    "support": ((structure.get("levels") or {}).get("support")) if isinstance(structure.get("levels"), dict) else None,
                    "resistance": ((structure.get("levels") or {}).get("resistance")) if isinstance(structure.get("levels"), dict) else None,
                },
                "positioning": {
                    "top10": positioning.get("top10"),
                    "top50": positioning.get("top50"),
                },
                "whale": {
                    "net": whale.get("net"),
                    "row_count": len(whale.get("rows") or []),
                },
            },
        }
        cards.append(
            PositionXKnowledgeCard(
                card_type="agent_prompt_pattern",
                key=f"analysis_pattern:{_slug(symbol)}",
                title=f"{symbol} Analysis Pattern",
                summary=summary,
                source_ref=f"analysis_samples.{_slug(symbol)}",
                source_hash=_stable_hash(safe_source),
                agent_template="Keep structure evidence compact: bias, rank, levels, positioning concentration, whale net summary, and one reasoning line; never include raw addresses, balances, or account fields.",
                risk_caveat="These examples show evidence organization only; they must not be replayed as direct trading instructions.",
                action_boundary=action_boundary,
            )
        )
    return cards


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _best_rank_hint(
    rows: list[dict[str, Any]],
    *,
    percentile_key: str,
    candidates: dict[str, Any],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for name, fn in candidates.items():
        pairs: list[tuple[float, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pct = ((row.get("pct") or {}) if isinstance(row.get("pct"), dict) else {}).get(percentile_key)
            if pct is None:
                continue
            value = fn(row)
            if value is None:
                continue
            pairs.append((float(value), float(pct)))
        correlation = _rank_correlation(pairs)
        if correlation is None:
            continue
        if best is None or abs(correlation) > abs(best["correlation"]):
            best = {"name": name, "correlation": round(correlation, 4)}
    return best


def _rank_correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    metric_ranks = compute_cross_sectional_percentiles(
        {str(idx): metric for idx, (metric, _) in enumerate(pairs)}
    )
    pct_ranks = compute_cross_sectional_percentiles(
        {str(idx): pct for idx, (_, pct) in enumerate(pairs)}
    )
    xs: list[float] = []
    ys: list[float] = []
    for idx in range(len(pairs)):
        x = metric_ranks.get(str(idx))
        y = pct_ranks.get(str(idx))
        if x is None or y is None:
            continue
        xs.append(float(x))
        ys.append(float(y))
    if len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / ((var_x ** 0.5) * (var_y ** 0.5))


def _metric_value(row: dict[str, Any], key: str) -> float:
    return _number(((row.get("metrics") or {}) if isinstance(row.get("metrics"), dict) else {}).get(key)) or 0.0


def _pct_text(value: Any) -> str:
    num = _number(value)
    if num is None:
        return ""
    if num <= 1:
        num *= 100.0
    return f"{num:.0f}%"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "sample"


def _loads_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_number(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and math.isfinite(number) and number > 0 else None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _direction_from_balance(bullish_value: float, bearish_value: float) -> str:
    if bullish_value > bearish_value:
        return "bullish"
    if bullish_value < bearish_value:
        return "bearish"
    return "neutral"


def _direction_from_vap(
    mark_price: float,
    value_area_low: float | None,
    value_area_high: float | None,
    poc: float | None,
) -> str:
    if value_area_low is None or value_area_high is None:
        return "neutral"
    if mark_price < value_area_low:
        return "bearish"
    if mark_price > value_area_high:
        return "bullish"
    if poc is None:
        return "neutral"
    if mark_price > poc:
        return "bullish"
    if mark_price < poc:
        return "bearish"
    return "neutral"


def _direction_from_proximity(mark_price: float, support: float | None, resistance: float | None) -> str:
    if support is None or resistance is None:
        return "neutral"
    support_gap = abs(mark_price - support)
    resistance_gap = abs(resistance - mark_price)
    if support_gap < resistance_gap:
        return "bullish"
    if support_gap > resistance_gap:
        return "bearish"
    return "neutral"
