"""Re-run the v2 golden fixture inputs through the v3 scorer to produce universe_step_score_v3.json.

Usage: uv run python scripts/build_universe_step_score_v3_golden.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from aletheia.config.schema import UniverseConfig
from aletheia.universe.allocator import compute_late_entry_penalty, compute_total_score
from aletheia.universe.filters import _compute_tradability_score
from aletheia.universe.scanners import (
    UNIVERSE_SCORER_BEHAVIOR_VERSION,
    run_all_scanners,
    score_derivatives,
    score_event,
    score_mean_reversion,
    score_momentum,
    score_short_bias,
    score_volatility,
)

_SCANNERS = {
    "momentum": score_momentum,
    "mean_reversion": score_mean_reversion,
    "volatility": score_volatility,
    "derivatives": score_derivatives,
    "event": score_event,
    "short_bias": score_short_bias,
}

_V2_PATH = Path(__file__).parent.parent / "tests/fixtures/universe_step_score_v2.json"
_V3_PATH = Path(__file__).parent.parent / "tests/fixtures/universe_step_score_v3.json"


def _run_scanner(component: str, feat: dict, cfg: UniverseConfig) -> object:
    if component in _SCANNERS:
        score, _reasons, direction = _SCANNERS[component](feat)
        return {"score": score, "direction": direction}
    if component == "run_all":
        result = run_all_scanners(feat)
        return {
            "score": result["opportunity_score"],
            "direction": result["scanner_direction"],
            "family": result["scanner_family"],
        }
    if component == "tradability":
        return _compute_tradability_score(feat, cfg)
    if component == "late_entry":
        return compute_late_entry_penalty(feat)
    if component == "total_score":
        scanner_result = {"opportunity_score": 50.0, "scanner_family": "momentum"}
        return compute_total_score(feat, scanner_result, cfg)
    raise ValueError(f"unknown component: {component!r}")


def _run_full_scanner(component: str, feat: dict) -> dict:
    score, reasons, direction = _SCANNERS[component](feat)
    return {"score": score, "reasons": reasons, "direction": direction}


def main() -> None:
    v2 = json.loads(_V2_PATH.read_text(encoding="utf-8"))
    cfg = UniverseConfig()
    neutral = v2["neutral_features"]
    v3 = copy.deepcopy(v2)
    v3["contract"] = "universe-step-score-golden/v3"
    v3["scorer_behavior_version"] = UNIVERSE_SCORER_BEHAVIOR_VERSION

    # Threshold probes
    for group in v3["threshold_probes"]:
        new_expected = []
        for value in group["values"]:
            feat = {**neutral, **group["base_overrides"], group["feature"]: value}
            new_expected.append(_run_scanner(group["component"], feat, cfg))
        group["expected"] = new_expected

    # Edge cases
    for case in v3["edge_cases"]:
        feat = {**neutral, **case["feature_overrides"]}
        if case["component"] in _SCANNERS:
            case["expected"] = _run_full_scanner(case["component"], feat)
        elif case["component"] == "tradability":
            case["expected"] = _compute_tradability_score(feat, cfg)
        elif case["component"] == "total_score":
            scanner_result = {"opportunity_score": 50.0, "scanner_family": "momentum"}
            case["expected"] = compute_total_score(feat, scanner_result, cfg)

    # Real vectors
    for case in v3["real_vectors"]:
        feat = {**neutral, **case["features"]}
        family = case["source_family"]
        score, reasons, direction = _SCANNERS[family](dict(feat))
        case["expected_family_result"] = {"score": score, "reasons": reasons, "direction": direction}
        winner = run_all_scanners(dict(feat))
        case["expected_winner"] = {
            "score": winner["opportunity_score"],
            "family": winner["scanner_family"],
            "direction": winner["scanner_direction"],
            "reasons": winner["reasons"],
            "is_proxy": winner["is_proxy"],
            "all_scores": winner["all_scores"],
        }

    _V3_PATH.write_text(json.dumps(v3, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Written {_V3_PATH}")


if __name__ == "__main__":
    main()
