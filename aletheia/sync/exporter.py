"""Export service — packages workspace data into transportable bundles."""

import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import sha256_file
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Secrets patterns that must never be exported
_SECRET_PATTERNS = {".env", ".key", ".pem", ".secret", "secrets/"}


def _is_secret_path(path: str) -> bool:
    for pat in _SECRET_PATTERNS:
        if pat in path.lower():
            return True
    return False


class ExportService:
    """Creates export bundles in various modes."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or get_data_dir()

    def export_bundle(
        self,
        mode: str = "full-backup",
        output: Path | None = None,
        since: str | None = None,
        proposal_id: str | None = None,
    ) -> Path:
        """Create an export bundle. Returns path to the archive."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        bundle_name = f"aletheia_export_{mode}_{timestamp}"
        exports_dir = self._data_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        bundle_dir = exports_dir / bundle_name
        bundle_dir.mkdir(parents=True, exist_ok=True)

        if mode == "config-only":
            self._export_config(bundle_dir)
        elif mode == "research-pack":
            self._export_research(bundle_dir, since)
        elif mode == "proposal-pack":
            self._export_proposal(bundle_dir, proposal_id)
        elif mode == "runtime-snapshot":
            self._export_runtime(bundle_dir)
        elif mode == "full-backup":
            self._export_full(bundle_dir)
        else:
            raise ValueError(f"Unknown export mode: {mode}")

        # Generate checksums
        self._write_checksums(bundle_dir)

        # Generate manifest
        self._write_manifest(bundle_dir, mode)

        # Create tar.gz archive
        archive_path = output or (exports_dir / f"{bundle_name}.tar.gz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_name)

        # Cleanup temp dir
        shutil.rmtree(bundle_dir)

        return archive_path

    def _export_config(self, bundle_dir: Path) -> None:
        """Export config and policy files only."""
        config_src = self._data_dir / "config.yaml"
        if config_src.exists():
            (bundle_dir / "config").mkdir(exist_ok=True)
            shutil.copy2(config_src, bundle_dir / "config" / "config.yaml")

    def _export_research(self, bundle_dir: Path, since: str | None) -> None:
        """Export evidence, claims, analyses, lessons, skills."""
        self._export_config(bundle_dir)
        self._export_db_tables(bundle_dir, [
            "serenity_themes", "serenity_evidence", "serenity_claims",
            "serenity_opportunities", "ps_lessons", "ps_skills",
        ], since=since)
        self._copy_artifacts(bundle_dir, ["evidence", "claims", "analyses", "lessons", "skills"])

    def _export_proposal(self, bundle_dir: Path, proposal_id: str | None) -> None:
        """Export a single proposal bundle."""
        if not proposal_id:
            raise ValueError("proposal_id required for proposal-pack export")
        self._export_db_tables(bundle_dir, [
            "trade_proposals", "serenity_themes", "serenity_evidence", "serenity_claims",
        ], where_clause=None)
        self._copy_artifacts(bundle_dir, ["proposals"])

    def _export_runtime(self, bundle_dir: Path) -> None:
        """Export open orders, positions, gateway state."""
        self._export_db_tables(bundle_dir, [
            "ps_orders", "ps_fills", "ps_positions", "ps_gateway_sessions",
            "ps_order_intents", "ps_execution_intents",
        ])

    def _export_full(self, bundle_dir: Path) -> None:
        """Export everything except secrets."""
        self._export_config(bundle_dir)
        # Copy entire DB
        db_path = self._data_dir / "app.db"
        if db_path.exists():
            (bundle_dir / "db").mkdir(exist_ok=True)
            shutil.copy2(db_path, bundle_dir / "db" / "app.db")
        # Copy events
        events_dir = self._data_dir / "events"
        if events_dir.exists():
            shutil.copytree(events_dir, bundle_dir / "events", dirs_exist_ok=True)
        # Copy artifacts (excluding secrets)
        self._copy_artifacts(bundle_dir, None)

    def _export_db_tables(self, bundle_dir: Path, tables: list[str], since: str | None = None, where_clause: str | None = None) -> None:
        """Export table data as JSONL files."""
        db = get_db()
        db_dir = bundle_dir / "db"
        db_dir.mkdir(exist_ok=True)

        for table in tables:
            sql = f"SELECT * FROM {table}"
            params: list = []
            if since:
                sql += " WHERE created_at > ?"
                params.append(since)
            try:
                rows = db.execute(sql, params).fetchall()
            except Exception:
                continue
            if not rows:
                continue
            with open(db_dir / f"{table}.jsonl", "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")

    def _copy_artifacts(self, bundle_dir: Path, categories: list[str] | None) -> None:
        """Copy artifact directories (excluding secrets)."""
        artifacts_src = self._data_dir / "artifacts"
        if not artifacts_src.exists():
            return
        artifacts_dst = bundle_dir / "artifacts"
        if categories is None:
            # Copy all
            for item in artifacts_src.iterdir():
                if item.is_dir() and not _is_secret_path(str(item)):
                    shutil.copytree(item, artifacts_dst / item.name, dirs_exist_ok=True)
        else:
            for cat in categories:
                src = artifacts_src / cat
                if src.exists():
                    shutil.copytree(src, artifacts_dst / cat, dirs_exist_ok=True)

    def _write_checksums(self, bundle_dir: Path) -> None:
        """Write checksums.txt with SHA-256 of all files."""
        lines = []
        for f in sorted(bundle_dir.rglob("*")):
            if f.is_file() and f.name != "checksums.txt":
                rel = f.relative_to(bundle_dir)
                h = sha256_file(f)
                lines.append(f"{h}  {rel}")
        (bundle_dir / "checksums.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_manifest(self, bundle_dir: Path, mode: str) -> None:
        """Write manifest.yaml."""
        import platform
        manifest = {
            "export_id": f"export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "created_at": _utcnow(),
            "created_by": platform.node() or "unknown",
            "aletheia_version": "0.1.0",
            "schema_version": "v1",
            "mode": mode,
            "secrets_included": False,
        }
        (bundle_dir / "manifest.yaml").write_text(
            yaml.dump(manifest, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
