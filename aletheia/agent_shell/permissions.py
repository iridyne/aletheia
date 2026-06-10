"""Permission Policy — maps config permission mode to allowed shell commands."""

from aletheia.agent_shell.registry import COMMANDS, FORBIDDEN_INTENTS

# Config modes in ascending privilege order.
_MODE_RANK = {"Observe": 0, "Paper": 1, "Full": 2}

# Map required_permission on CommandDef to the minimum mode rank needed.
_REQUIRED_RANK = {"Observe": 0, "Paper": 1, "Full": 2}


def check_permission(command_name: str, current_mode: str) -> tuple[bool, str | None]:
    """Check if `command_name` is allowed under `current_mode`.

    Returns (allowed, denial_reason). If allowed, denial_reason is None.
    """
    if command_name in FORBIDDEN_INTENTS:
        return False, f"'{command_name}' is permanently forbidden — the Agent Shell never has access to live trading, credentials, or hard risk limits."

    cmd = COMMANDS.get(command_name)
    if cmd is None:
        return False, f"Unknown command '{command_name}'."

    mode_rank = _MODE_RANK.get(current_mode, 0)
    required_rank = _REQUIRED_RANK.get(cmd.required_permission, 99)

    if mode_rank >= required_rank:
        return True, None

    return False, (
        f"Command '{command_name}' requires permission mode '{cmd.required_permission}' "
        f"but current mode is '{current_mode}'. "
        f"Change permission mode via Settings to enable this command."
    )


def allowed_commands(current_mode: str) -> list[str]:
    """Return list of command names allowed under the given mode."""
    mode_rank = _MODE_RANK.get(current_mode, 0)
    return [
        name for name, cmd in COMMANDS.items()
        if _REQUIRED_RANK.get(cmd.required_permission, 99) <= mode_rank
    ]
