"""Pre-commit hook helper: check staged paths against .agent/policy.yaml.

Run with: uv run python scripts/agent_check_staged.py --paths-stdin
Reads one path per line from stdin. Exits 0 when no trust boundary is touched or
every touched boundary is covered by a passing task packet; exits 1 otherwise.
"""
import sys

from aletheia.agent_workflow import load_tasks, repo_root, trust_boundary_hits_for_paths


def main() -> None:
    if "--paths-stdin" not in sys.argv:
        print("--paths-stdin is required; pipe staged paths on stdin", file=sys.stderr)
        sys.exit(2)
    changed = {line.strip() for line in sys.stdin.read().splitlines() if line.strip()}
    root = repo_root()
    hits = trust_boundary_hits_for_paths(changed, root=root)
    if not hits:
        sys.exit(0)
    missing = []
    task_packets = [task for _, task in load_tasks(root)]
    for boundary_id, meta in sorted(hits.items()):
        required = set(meta["required_command_keys"])
        satisfied = any(
            isinstance(item, dict)
            and item.get("exit_code") == 0
            and item.get("result", "passed") == "passed"
            and item.get("command_key") in required
            for task in task_packets
            for item in task.get("verification", [])
        )
        if not satisfied:
            paths_summary = ", ".join(meta["paths"][:3])
            if len(meta["paths"]) > 3:
                paths_summary += ", ..."
            missing.append(
                f"trust-boundary {boundary_id!r} touched ({paths_summary}); "
                f"stage a task packet with passing evidence for one of: "
                f"{', '.join(sorted(required))}"
            )
    if missing:
        for line in missing:
            print(line, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
