"""Agent permission enforcement."""

TOOL_CLASSES = {
    "observe": {"Observe", "Paper", "Full"},
    "paper": {"Paper", "Full"},
    "system": {"Full"},
    "restricted": set(),
}

TOOL_REGISTRY: dict[str, str] = {}


def register_tool(name: str, tool_class: str) -> None:
    TOOL_REGISTRY[name] = tool_class


def is_tool_permitted(tool_name: str, permission_mode: str) -> bool:
    """Check if a tool is permitted under the current permission mode."""
    tool_class = TOOL_REGISTRY.get(tool_name, "restricted_tool")
    allowed_modes = TOOL_CLASSES.get(tool_class, set())
    return permission_mode in allowed_modes


def get_permitted_tools(permission_mode: str) -> list[str]:
    """Get list of tools permitted for the given mode."""
    return [name for name, cls in TOOL_REGISTRY.items()
            if permission_mode in TOOL_CLASSES.get(cls, set())]
