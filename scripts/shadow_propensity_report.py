"""CLI wrapper for build_propensity_report().

Usage:
    uv run python scripts/shadow_propensity_report.py
    uv run python scripts/shadow_propensity_report.py --days 30
    uv run python scripts/shadow_propensity_report.py --family short_bias --json
    uv run python scripts/shadow_propensity_report.py --policy-epoch <id>
"""
from __future__ import annotations

import argparse
import json
import sys


def _fmt_stats(stats: dict) -> str:
    return (
        f"  mean={stats['mean']:.4f}  "
        f"p5={stats['p5']:.4f}  p50={stats['p50']:.4f}  p95={stats['p95']:.4f}  "
        f"[{stats['min']:.4f}, {stats['max']:.4f}]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow-trades propensity model report")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--family", dest="scanner_family", default=None,
                        help="Restrict to one scanner family")
    parser.add_argument("--policy-epoch", dest="policy_epoch_id", default=None,
                        help="Restrict to one policy epoch ID")
    parser.add_argument("--min-samples", type=int, default=50,
                        help="Minimum resolved rows required (default: 50)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Print full report as JSON")
    args = parser.parse_args()

    from aletheia.counterfactual.propensity import build_propensity_report

    report = build_propensity_report(
        days=args.days,
        scanner_family=args.scanner_family,
        policy_epoch_id=args.policy_epoch_id,
        min_samples=args.min_samples,
    )

    if args.as_json:
        print(json.dumps(report, indent=2))
        sys.exit(1 if report["errors"] else 0)

    # --- Human-readable output ---
    print(f"\n{'='*60}")
    print(f"  Propensity Report  (window={report['window_days']}d, k={report['k_folds']})")
    print(f"{'='*60}")

    if report["errors"]:
        for e in report["errors"]:
            print(f"  ERROR: {e}")
        sys.exit(1)

    print(f"  rows   : {report['n_total']}  (treated={report['n_treated']}, control={report['n_control']})")
    print(f"  rate   : {report['treatment_rate']:.1%}  selected")
    print(f"  AUC    : {report['auc']:.4f}  (OOS cross-fit)")
    print(f"\n  propensity distribution (clipped):")
    print(_fmt_stats(report["propensity_stats"]))

    print(f"\n  per-family breakdown:")
    for fam, stats in sorted(report["by_family"].items()):
        rate = f"{stats['actual_selection_rate']:.1%}" if stats["actual_selection_rate"] is not None else "n/a"
        prop = f"{stats['mean_propensity']:.4f}" if stats["mean_propensity"] is not None else "n/a"
        print(f"    {fam:<18} n={stats['n']:>5}  actual={rate}  propensity={prop}")

    if report["warnings"]:
        print(f"\n  warnings:")
        for w in report["warnings"]:
            print(f"    ⚠  {w}")

    print(f"\n  features: {report['feature_names']}")
    coef_pairs = list(zip(report["feature_names"], report["coefficients"][:-1]))
    bias = report["coefficients"][-1] if report["coefficients"] else None
    print(f"  coefficients:")
    for fname, coef in coef_pairs:
        print(f"    {fname:<22} {coef:+.4f}")
    if bias is not None:
        print(f"    {'bias':<22} {bias:+.4f}")

    print()


if __name__ == "__main__":
    main()
