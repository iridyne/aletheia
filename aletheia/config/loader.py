"""Config loader: reads/writes data/config.yaml, bootstraps defaults."""

from pathlib import Path

import yaml

from aletheia.config.schema import AletheiaConfig

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.yaml"


def get_data_dir() -> Path:
    return DATA_DIR


def ensure_data_dirs() -> None:
    """Create the full data directory tree on first run."""
    dirs = [
        DATA_DIR,
        DATA_DIR / "logs",
        DATA_DIR / "knowledge" / "evidence",
        DATA_DIR / "knowledge" / "research_notes",
        DATA_DIR / "knowledge" / "strategies",
        DATA_DIR / "knowledge" / "agent_memory",
        DATA_DIR / "knowledge" / "daily_reviews",
        DATA_DIR / "knowledge" / "dreaming_logs",
        DATA_DIR / "knowledge" / "post_trade_reviews",
        DATA_DIR / "knowledge" / "deprecated",
        DATA_DIR / "playbooks",
        DATA_DIR / "reports" / "daily",
        DATA_DIR / "reports" / "post_trade",
        DATA_DIR / "reports" / "system_health",
        DATA_DIR / "exports",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> AletheiaConfig:
    """Load config from disk or return defaults."""
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return AletheiaConfig.model_validate(raw)
    return AletheiaConfig()


def save_config(cfg: AletheiaConfig) -> None:
    """Persist config to disk."""
    ensure_data_dirs()
    data = cfg.model_dump(mode="json")
    CONFIG_PATH.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")


def bootstrap() -> AletheiaConfig:
    """Ensure data dirs exist and config is written. Returns loaded config."""
    ensure_data_dirs()
    # Seed starter playbooks on first run (guide §22.3 / §8)
    try:
        from aletheia.trading.playbooks import seed_playbooks
        seed_playbooks(DATA_DIR)
    except Exception:
        pass
    if not CONFIG_PATH.exists():
        cfg = AletheiaConfig()
        save_config(cfg)
        return cfg
    return load_config()
