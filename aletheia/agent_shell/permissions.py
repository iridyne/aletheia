"""Permission Policy — maps config permission mode to allowed shell commands."""

from aletheia.agent_shell.registry import COMMANDS, FORBIDDEN_INTENTS

# Config modes in ascending privilege order for worker-surface commands.
_WORKER_MODE_RANK = {"Observe": 0, "Paper": 1, "Full": 2}
_WORKER_REQUIRED_RANK = {"Observe": 0, "Paper": 1, "Full": 2}

# Operator-surface permission ladder from the design package.
_OPERATOR_MODE_RANK = {
    "ReadOnly": 0,
    "Research": 1,
    "PaperAuto": 2,
    "AllowEditing": 3,
    "Auto": 4,
}
_OPERATOR_REQUIRED_RANK = {
    "ReadOnly": 0,
    "Research": 1,
    "PaperAuto": 2,
    "AllowEditing": 3,
    "Auto": 4,
}


def _permission_context(permission_scope: str) -> tuple[str, dict[str, int], dict[str, int], str]:
    if permission_scope == "operator":
        return (
            "operator_permission_required",
            _OPERATOR_MODE_RANK,
            _OPERATOR_REQUIRED_RANK,
            "operator",
        )
    return (
        "worker_permission_required",
        _WORKER_MODE_RANK,
        _WORKER_REQUIRED_RANK,
        "worker",
    )


def check_permission(
    command_name: str,
    current_mode: str,
    *,
    permission_scope: str = "worker",
) -> tuple[bool, str | None]:
    """Check if `command_name` is allowed under `current_mode`.

    Returns (allowed, denial_reason). If allowed, denial_reason is None.
    """
    if command_name in FORBIDDEN_INTENTS:
        return False, f"'{command_name}' is permanently forbidden — the Agent Shell never has access to live trading, credentials, or hard risk limits."

    cmd = COMMANDS.get(command_name)
    if cmd is None:
        return False, f"Unknown command '{command_name}'."

    permission_attr, mode_ranks, required_ranks, scope_label = _permission_context(permission_scope)
    required_mode = getattr(cmd, permission_attr)

    mode_rank = mode_ranks.get(current_mode, 0)
    required_rank = required_ranks.get(required_mode, 99)

    if mode_rank >= required_rank:
        return True, None

    return False, (
        f"Command '{command_name}' requires {scope_label} permission mode '{required_mode}' "
        f"but current mode is '{current_mode}'. "
        f"Change permission mode via Settings to enable this command."
    )


def allowed_commands(current_mode: str, *, permission_scope: str = "worker") -> list[str]:
    """Return list of command names allowed under the given mode."""
    permission_attr, mode_ranks, required_ranks, _ = _permission_context(permission_scope)
    mode_rank = mode_ranks.get(current_mode, 0)
    return [
        name for name, cmd in COMMANDS.items()
        if required_ranks.get(getattr(cmd, permission_attr), 99) <= mode_rank
    ]
