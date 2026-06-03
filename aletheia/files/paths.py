"""Common file path helpers."""

from pathlib import Path
from aletheia.config.loader import get_data_dir


def knowledge_dir() -> Path:
    return get_data_dir() / "knowledge"


def playbooks_dir() -> Path:
    return get_data_dir() / "playbooks"


def reports_dir() -> Path:
    return get_data_dir() / "reports"


def logs_dir() -> Path:
    return get_data_dir() / "logs"
