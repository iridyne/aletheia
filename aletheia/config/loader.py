"""Config loader: reads/writes data/config.yaml, bootstraps defaults."""

from pathlib import Path

import yaml

from aletheia.config.schema import AletheiaConfig

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.yaml"

_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Load .env from project root if present. Idempotent, never raises."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:
        import os
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if value and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            os.environ.setdefault(key, value)


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
        DATA_DIR / "reports" / "trade_review",
        DATA_DIR / "reports" / "risk",
        DATA_DIR / "reports" / "agent_performance",
        DATA_DIR / "exports",
        # Persistence / Sync / Recovery workspace directories
        DATA_DIR / "events",
        DATA_DIR / "backups",
        DATA_DIR / "artifacts" / "evidence",
        DATA_DIR / "artifacts" / "claims",
        DATA_DIR / "artifacts" / "market_snapshots",
        DATA_DIR / "artifacts" / "analyses",
        DATA_DIR / "artifacts" / "opportunities",
        DATA_DIR / "artifacts" / "strategy_specs",
        DATA_DIR / "artifacts" / "validation_reports",
        DATA_DIR / "artifacts" / "proposals",
        DATA_DIR / "artifacts" / "paper_runs",
        DATA_DIR / "artifacts" / "live_runs",
        DATA_DIR / "artifacts" / "reconciliation_reports",
        DATA_DIR / "artifacts" / "reviews",
        DATA_DIR / "artifacts" / "lessons",
        DATA_DIR / "artifacts" / "skills",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


_MODE_MIGRATION = {
    "ReadOnly": "Observe", "Research": "Observe",
    "PaperAuto": "Paper",
    "AllowEditing": "Full", "Auto": "Full",
}

_OPERATOR_MODE_MIGRATION = {
    "Observe": "ReadOnly",
    "Paper": "PaperAuto",
    "Full": "Auto",
}


def load_config() -> AletheiaConfig:
    """Load config from disk or return defaults."""
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        cfg = AletheiaConfig.model_validate(raw)
        changed = False
        if cfg.agent.permission_mode in _MODE_MIGRATION:
            cfg.agent.permission_mode = _MODE_MIGRATION[cfg.agent.permission_mode]
            changed = True
        if cfg.operator.permission_mode in _OPERATOR_MODE_MIGRATION:
            cfg.operator.permission_mode = _OPERATOR_MODE_MIGRATION[cfg.operator.permission_mode]
            changed = True
        if changed:
            save_config(cfg)
        return cfg
    return AletheiaConfig()


import tempfile
import threading

_config_write_lock = threading.Lock()


def save_config(cfg: AletheiaConfig) -> None:
    """Persist config to disk (atomic write + thread lock)."""
    ensure_data_dirs()
    data = cfg.model_dump(mode="json")
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    with _config_write_lock:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(CONFIG_PATH.parent), suffix=".tmp"
        )
        try:
            import os
            os.write(tmp_fd, content.encode("utf-8"))
            os.close(tmp_fd)
            os.replace(tmp_path, str(CONFIG_PATH))
        except BaseException:
            os.close(tmp_fd) if not os.get_inheritable(tmp_fd) else None
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    _emit_consistency_events(cfg)


def _emit_consistency_events(cfg: AletheiaConfig) -> None:
    try:
        from aletheia.diagnostics.config_consistency import check_config_consistency
        from aletheia.infra.events import emit_infra_event
        for finding in check_config_consistency(cfg):
            emit_infra_event(
                severity="warning",
                source="config_consistency",
                code=finding.field_path,
                message=finding.message,
                action=finding.fix_hint,
                dedupe_sec=300,
            )
    except Exception:  # noqa: BLE001 — advisory; must never break save
        pass


def bootstrap() -> AletheiaConfig:
    """Ensure data dirs exist and config is written. Returns loaded config."""
    _load_dotenv()
    ensure_data_dirs()
    # Seed starter playbooks on first run (guide §22.3 / §8)
    try:
        from aletheia.trading.playbooks import seed_playbooks
        seed_playbooks(DATA_DIR)
    except Exception as e:
        import logging
        logging.getLogger("aletheia.config").warning("bootstrap: seed_playbooks failed: %s", e)
    if not CONFIG_PATH.exists():
        cfg = AletheiaConfig()
        save_config(cfg)
        return cfg
    return load_config()
