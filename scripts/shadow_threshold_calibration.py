#!/usr/bin/env python3
"""shadow_threshold_calibration — thin CLI wrapper.

The report logic lives in ``aletheia.counterfactual.calibration``; this script
only parses arguments and prints. Read-only; no writes to DB, filesystem, or
configuration; no LLM / exchange calls.

Usage
-----
    uv run python scripts/shadow_threshold_calibration.py
    uv run python scripts/shadow_threshold_calibration.py --days 14
    uv run python scripts/shadow_threshold_calibration.py --symbol SOXL/USDT --symbol CHZ/USDT
    uv run python scripts/shadow_threshold_calibration.py --policy-epoch <id>
    uv run python scripts/shadow_threshold_calibration.py --json
"""
from __future__ import annotations

import argparse
import json
import sys

from aletheia.counterfactual.calibration import build_calibration, render_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__ or "",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=8, help="Lookback window in days (default 8).")
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Canonical symbol to scope to (e.g. SOXL/USDT). Repeatable.",
    )
    parser.add_argument(
        "--policy-epoch",
        dest="policy_epoch_id",
        default=None,
        help="Restrict to shadow rows written under this policy epoch id.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args(argv)

    report = build_calibration(
        days=args.days,
        symbols=args.symbols,
        policy_epoch_id=args.policy_epoch_id,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
