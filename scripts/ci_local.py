#!/usr/bin/env python3
"""Run the local checks selected by scripts/ci_plan.py."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_plan  # noqa: E402

SCHEMA_VERSION = "local_ci.v1"


def _run(command: list[str], cwd: str | None = None) -> dict[str, Any]:
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, check=False)
    return {
        "command": command,
        "working_directory": cwd or ".",
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repository-native local CI.")
    parser.add_argument("--pr-body-file", required=True)
    parser.add_argument("--base-ref")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--changed-files-complete", choices=("true", "false"), default="true")
    parser.add_argument("--skip-execute", action="store_true")
    args = parser.parse_args(argv)

    base_sha, head_sha, changes = ci_plan._load_changes(argparse.Namespace(  # noqa: SLF001
        base_sha=None,
        head_sha=None,
        base_ref=args.base_ref,
        head_ref="HEAD",
        event="local",
        changed_file=args.changed_file,
        changed_files_json=None,
        changed_files_complete=args.changed_files_complete,
    ))
    report = ci_plan.plan(
        event="local",
        base_sha=base_sha,
        head_sha=head_sha,
        changes=changes,
        changed_files_complete=args.changed_files_complete == "true",
    )
    changed_files = [
        change["path"] for change in report["changed_files"]
    ]
    preflight_command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        "scripts/ci_preflight.py",
        "--pr-body-file",
        args.pr_body_file,
        "--changed-files",
        *changed_files,
    ]
    local_checks = []
    for check in report["local_checks"]:
        if check.get("command") == ["git", "diff", "--check"]:
            check = {
                **check,
                "command": ["git", "diff", "--check", f"{report['base_sha']}...{report['head_sha']}", "--"],
            }
        local_checks.append(check)
    checks = [{"name": "change contract preflight", "command": preflight_command}, *local_checks]
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_fingerprint": report["plan_fingerprint"],
        "base_sha": report["base_sha"],
        "head_sha": report["head_sha"],
        "mode": report["mode"],
        "surfaces": report["surfaces"],
        "required": report["required"],
        "checks": [{**check, "status": "not_run"} for check in checks],
        "environment": {"platform": sys.platform, "python": sys.version.split()[0]},
        "not_proven": ["deployed runtime health", "external connectivity", "economic qualification"],
        "result": "pass",
    }
    if args.skip_execute:
        receipt["result"] = "planned"
    exit_code = 0
    if not args.skip_execute:
        for check in receipt["checks"]:
            if check.get("unavailable_reason"):
                check["status"] = "required_but_unavailable"
                receipt["result"] = "required_but_unavailable"
                receipt["failure_reason"] = check["unavailable_reason"]
                exit_code = 2
                break
            outcome = _run(check["command"], check.get("working_directory"))
            check.update(outcome)
            check["status"] = "pass" if outcome["exit_code"] == 0 else "fail"
            if outcome["exit_code"] != 0:
                receipt["result"] = "fail"
                receipt["failure_reason"] = f"{check['name']} failed"
                exit_code = outcome["exit_code"] or 1
                break
    executed = {check.get("lane") for check in receipt["checks"] if check["status"] in {"pass", "fail"}}
    receipt["not_executed_surfaces"] = [
        surface for surface, lanes in {
            "python": {"python_unit", "runtime", "integration", "specifications", "e2e", "coverage"},
            "operator": {"operator"}, "frontend": {"frontend"}, "native": {"native"},
        }.items() if not executed.intersection(lanes)
    ]
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
