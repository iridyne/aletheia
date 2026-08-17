"""Config loader for the current user's Aletheia runtime home."""

import os
import tempfile
import threading
import time
from pathlib import Path

import yaml

from aletheia.config.schema import AletheiaConfig
from aletheia.runtime_paths import preview_root, runtime_home

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PREVIEW_MODE_ENV = "ALETHEIA_PREVIEW_MODE"
_PREVIEW_DATA_DIR_ENV = "ALETHEIA_PREVIEW_DATA_DIR"


def is_preview_mode() -> bool:
    """Whether this process is an explicitly isolated development API."""
    return os.environ.get(_PREVIEW_MODE_ENV, "").strip() == "1"


def preview_data_dir() -> Path:
    """Return the only data root an isolated preview process may use.

    The CLI supplies this value.  Constraining it to this checkout prevents an
    accidental environment override from attaching a preview process to the
    primary runtime's state directory.
    """
    raw = os.environ.get(_PREVIEW_DATA_DIR_ENV, "").strip()
    if not raw:
        raise RuntimeError("preview mode requires ALETHEIA_PREVIEW_DATA_DIR")
    candidate = Path(raw).expanduser().resolve()
    allowed_root = preview_root().resolve()
    if not candidate.is_relative_to(allowed_root):
        raise RuntimeError("preview data directory must be under ~/.aletheia/instances/dev-preview")
    return candidate


def runtime_data_dir() -> Path:
    """Return the durable data root for the primary runtime.

    Release bundles and development checkouts may live anywhere while sharing one
    per-user state root. ``ALETHEIA_DATA_DIR`` remains an absolute override.
    Preview mode deliberately ignores it and keeps stricter instance isolation.
    """
    return runtime_home()


DATA_DIR = preview_data_dir() if is_preview_mode() else runtime_data_dir()
CONFIG_PATH = DATA_DIR / "config.yaml"

_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Load .env from project root if present. Idempotent, never raises."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_file = DATA_DIR / ".env"
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


# ensure_data_dirs tracks the last DATA_DIR it ran for; re-runs only when
# DATA_DIR changes (e.g. in tests with monkeypatched tmp paths).
_dirs_ensured_for: Path | None = None


def ensure_data_dirs() -> None:
    """Create the full data directory tree (once per unique DATA_DIR)."""
    global _dirs_ensured_for
    if _dirs_ensured_for == DATA_DIR:
        return
    if not is_preview_mode():
        from aletheia.runtime_home_migration import assert_runtime_home_ready

        assert_runtime_home_ready(
            project_root=_PROJECT_ROOT,
            target=DATA_DIR,
            override_active=bool(os.environ.get("ALETHEIA_DATA_DIR", "").strip()),
        )
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
    _dirs_ensured_for = DATA_DIR


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

# ─── In-memory config cache ───────────────────────────────────────────────────
# Avoids repeated disk reads on every API request.  TTL = 5 s; invalidated
# immediately by save_config() so mutations are always reflected.
# Cache key includes CONFIG_PATH so monkeypatched test paths don't collide.
_config_write_lock = threading.Lock()
_config_cache: "tuple[float, Path, AletheiaConfig] | None" = None
_CONFIG_CACHE_TTL = 5.0


def _invalidate_config_cache() -> None:
    global _config_cache
    _config_cache = None


def load_config(*, persist_migrations: bool = True) -> AletheiaConfig:
    """Load config from disk or return defaults.  Hot path uses in-memory cache.

    #147 fix: return a DEEP COPY of the cached config, not the cached object
    itself. Route handlers routinely do
        cfg = load_config(); cfg.risk.foo = new_value; save_config(cfg)
    — if save_config rejects (validation error, disk failure), the caller's
    mutated cfg is the SAME OBJECT that was in the cache, so any concurrent
    load_config call inside the 5s TTL sees the phantom mutation. Deep-copy
    on the read side makes mutations local to each caller; the cache stays
    the source of truth on disk.
    """
    global _config_cache
    if not is_preview_mode():
        from aletheia.runtime_home_migration import assert_runtime_home_ready

        assert_runtime_home_ready(
            project_root=_PROJECT_ROOT,
            target=DATA_DIR,
            override_active=bool(os.environ.get("ALETHEIA_DATA_DIR", "").strip()),
        )
    if _config_cache is not None:
        ts, cached_path, cfg = _config_cache
        if time.monotonic() - ts < _CONFIG_CACHE_TTL and cached_path == CONFIG_PATH:
            return cfg.model_copy(deep=True)
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
        if changed and persist_migrations:
            save_config(cfg)
        if changed and not persist_migrations:
            return cfg.model_copy(deep=True)
        _config_cache = (time.monotonic(), CONFIG_PATH, cfg)
        return cfg.model_copy(deep=True)
    cfg = AletheiaConfig()
    _config_cache = (time.monotonic(), CONFIG_PATH, cfg)
    return cfg.model_copy(deep=True)


def _save_config_locked(cfg: AletheiaConfig) -> None:
    """Persist config while the process-wide config write lock is held."""
    _invalidate_config_cache()
    ensure_data_dirs()
    cfg.runtime.set_pause_scope(cfg.exchange.mode)
    data = cfg.model_dump(mode="json")
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
    try:
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


def save_config(cfg: AletheiaConfig) -> None:
    """Persist config to disk (atomic write + thread lock)."""
    with _config_write_lock:
        _save_config_locked(cfg)
    _emit_consistency_events(cfg)


def mutate_config(mutator):
    """Apply one mutation to the latest config snapshot under the write lock."""
    with _config_write_lock:
        cfg = load_config(persist_migrations=False)
        changed = bool(mutator(cfg))
        if changed:
            _save_config_locked(cfg)
    if changed:
        _emit_consistency_events(cfg)
    return changed


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
