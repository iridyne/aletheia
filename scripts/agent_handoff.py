"""Compatibility wrapper. Run with: uv run python scripts/agent_handoff.py TASK-ID handoff.md"""
import sys
from pathlib import Path
from aletheia.agent_workflow import write_handoff

if len(sys.argv) != 3:
    raise SystemExit("usage: uv run python scripts/agent_handoff.py TASK-ID HANDOFF.md")
path = write_handoff(sys.argv[1], Path(sys.argv[2]).read_text(encoding="utf-8"))
print(f"Updated {path}; stage it and the matching .agent/tasks file.")
