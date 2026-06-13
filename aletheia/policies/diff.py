"""Policy diff — human-readable comparison between policy versions."""

import json
from typing import Any

from aletheia.policies.store import PolicyStore


def diff_policy_versions(version_id_from: str, version_id_to: str) -> dict:
    """Generate a human-readable diff between two policy versions.

    Returns a dict with:
      - added: keys/rules present in to_version but not from_version
      - removed: keys/rules present in from_version but not to_version
      - changed: keys where value differs
      - unchanged_count: number of rules unchanged
    """
    store = PolicyStore()
    v_from = store.get_version(version_id_from)
    v_to = store.get_version(version_id_to)

    if v_from is None:
        raise ValueError(f"Policy version '{version_id_from}' not found")
    if v_to is None:
        raise ValueError(f"Policy version '{version_id_to}' not found")

    rules_from = json.loads(v_from.policy_json)
    rules_to = json.loads(v_to.policy_json)

    return _diff_dicts(rules_from, rules_to)


def diff_dicts(old: dict, new: dict) -> dict:
    """Public interface to compare two arbitrary rule dicts."""
    return _diff_dicts(old, new)


def _diff_dicts(old: dict, new: dict) -> dict:
    """Compare two rule dictionaries."""
    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    changed: dict[str, dict] = {}
    unchanged = 0

    all_keys = set(old.keys()) | set(new.keys())
    for key in sorted(all_keys):
        if key not in old:
            added[key] = new[key]
        elif key not in new:
            removed[key] = old[key]
        elif old[key] != new[key]:
            changed[key] = {"from": old[key], "to": new[key]}
        else:
            unchanged += 1

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged,
        "summary": _make_summary(added, removed, changed),
    }


def _make_summary(added: dict, removed: dict, changed: dict) -> str:
    """One-line human summary."""
    parts = []
    if added:
        parts.append(f"+{len(added)} added")
    if removed:
        parts.append(f"-{len(removed)} removed")
    if changed:
        parts.append(f"~{len(changed)} changed")
    if not parts:
        return "No changes"
    return ", ".join(parts)
