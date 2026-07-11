"""Compatibility wrapper. Run with: uv run python scripts/agent_bootstrap.py"""
import sys
from aletheia.agent_workflow import bootstrap, render_bootstrap

payload = bootstrap()
print(render_bootstrap(payload, json_out="--json" in sys.argv))
raise SystemExit(0 if payload["task"] is not None and payload["task_match_count"] <= 1 and not any(x["severity"] == "error" for x in payload["findings"]) else 1)
