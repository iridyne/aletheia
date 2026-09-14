"""Agent registry — maps agent names to their run functions."""

from typing import Callable, Awaitable

_AGENTS: dict[str, Callable[..., Awaitable[dict]]] = {}


def register_agent(name: str):
    """Decorator to register an agent function."""
    def decorator(fn):
        _AGENTS[name] = fn
        return fn
    return decorator


def get_agent_fn(name: str) -> Callable[..., Awaitable[dict]]:
    if name not in _AGENTS:
        # Try importing the module to trigger registration
        import importlib
        try:
            importlib.import_module(f"aletheia.agents.{name}")
        except ImportError:
            pass
    if name not in _AGENTS:
        raise ValueError(f"Unknown agent: {name}")
    return _AGENTS[name]
