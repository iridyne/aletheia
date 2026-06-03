"""Gateway log management."""

from pathlib import Path

from aletheia.config.loader import get_data_dir


def log_path() -> Path:
    return get_data_dir() / "logs" / "gateway.log"


def tail_logs(lines: int = 50) -> list[str]:
    """Return the last N lines of the gateway log."""
    p = log_path()
    if not p.exists():
        return []
    all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return all_lines[-lines:]
