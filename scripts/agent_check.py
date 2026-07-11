"""Compatibility wrapper. Run with: uv run python scripts/agent_check.py --strict"""
import sys
from aletheia.agent_workflow import validate

findings = validate(strict="--strict" in sys.argv)
for item in findings:
    print(f"{item.severity.upper()}: {item.message}" + (f" [{item.task_id}]" if item.task_id else ""))
raise SystemExit(1 if any(item.severity == "error" for item in findings) else 0)
