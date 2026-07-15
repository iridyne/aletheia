"""Cold-session recovery assessment. Run with: uv run python scripts/agent_recovery_test.py [--json]"""
import json
import sys

from aletheia.agent_workflow import recovery_test

payload = recovery_test()
if "--json" in sys.argv:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
else:
    print("RECOVERABLE" if payload["recoverable"] else "NOT RECOVERABLE")
raise SystemExit(0 if payload["recoverable"] else 1)
