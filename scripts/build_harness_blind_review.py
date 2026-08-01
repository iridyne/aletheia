#!/usr/bin/env python3
"""Build a deterministic, arm-blinded PI review packet from ablation output."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.harness_header_ablation import _facts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=30)
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))["rows"]
    pi_rows = [row for row in rows if row["caller"] == "pi_trading_decision"]
    by_case: dict[str, dict[str, dict]] = {}
    for row in pi_rows:
        by_case.setdefault(row["case_id"], {})[row["arm"]] = row

    packet = []
    for case_id in sorted(by_case)[: args.count]:
        pair = by_case[case_id]
        case_index = int(case_id.rsplit("-", 1)[1])
        swap = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 2 == 1
        ordered = [pair["header"], pair["control"]] if swap else [pair["control"], pair["header"]]
        packet.append({
            "case_id": case_id,
            "case_facts": _facts(case_index),
            "response_a": ordered[0]["content"],
            "response_b": ordered[1]["content"],
            "scores": {
                "response_a": {"evidence_fidelity_1_5": None, "calibration_1_5": None, "decision_quality_1_5": None},
                "response_b": {"evidence_fidelity_1_5": None, "calibration_1_5": None, "decision_quality_1_5": None},
                "preferred": None,
                "notes": "",
            },
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
