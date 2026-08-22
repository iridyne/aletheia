"""Export service — packages workspace data into transportable bundles."""

import json
import shutil
import sqlite3
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


def _secret_ignore(dirpath, names):
    """shutil.copytree ignore callback — skip secret files/dirs at ANY depth, not just top level."""
    return [n for n in names if _is_secret_path(n) or n.lower() == "secrets"]


from aletheia.config.redact import redact_config_dict as _redact_config_dict


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
        """Export config with credential-bearing fields redacted (keeps manifest secrets_included=False honest)."""
        config_src = self._data_dir / "config.yaml"
        if config_src.exists():
            (bundle_dir / "config").mkdir(exist_ok=True)
            dst = bundle_dir / "config" / "config.yaml"
            try:
                data = yaml.safe_load(config_src.read_text(encoding="utf-8")) or {}
                redacted = _redact_config_dict(data)
                dst.write_text(yaml.safe_dump(redacted, default_flow_style=False, sort_keys=False), encoding="utf-8")
            except Exception:
                # Fail closed: never fall back to copying the raw file (that would leak).
                dst.write_text("# config.yaml omitted from export: could not parse to redact secrets\n", encoding="utf-8")

    def _export_research(self, bundle_dir: Path, since: str | None) -> None:
        """Export evidence, claims, analyses, lessons, skills."""
        self._export_config(bundle_dir)
        self._export_db_tables(bundle_dir, [
            "serenity_themes", "serenity_evidence", "serenity_claims",
            "serenity_opportunities", "ps_lessons", "ps_skills",
        ], since=since)
        self._copy_artifacts(bundle_dir, ["evidence", "claims", "analyses", "lessons", "skills"])

    def _export_proposal(self, bundle_dir: Path, proposal_id: str | None) -> None:
        """Export a single proposal bundle.

        #62/#162 fix: was raising on missing proposal_id but then ignoring it —
        the follow-up _export_db_tables call had `where_clause=None`, so a call
        that named ONE proposal dumped every trade_proposals row (and every
        serenity_themes/evidence/claims row) into the bundle. Now the proposal
        is filtered by id; serenity rows are filtered through the proposal's
        opportunity_id → theme_id chain when resolvable, else the file is
        omitted rather than exporting the entire table under a misleading
        proposal-pack label.
        """
        if not proposal_id:
            raise ValueError("proposal_id required for proposal-pack export")

        db = get_db()
        proposal_row = db.execute(
            "SELECT * FROM trade_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if proposal_row is None:
            raise ValueError(f"proposal-pack: proposal {proposal_id!r} not found")

        db_dir = bundle_dir / "db"
        db_dir.mkdir(exist_ok=True)
        self._write_rows(db_dir / "trade_proposals.jsonl", [dict(proposal_row)])

        # Resolve the serenity chain if this proposal was serenity-attributed.
        opportunity_id = proposal_row["opportunity_id"] if "opportunity_id" in proposal_row.keys() else None
        theme_ids: set[str] = set()
        opp_rows: list[dict] = []
        if opportunity_id:
            try:
                opps = db.execute(
                    "SELECT * FROM serenity_opportunities WHERE id = ?", (opportunity_id,)
                ).fetchall()
                opp_rows = [dict(r) for r in opps]
                for r in opp_rows:
                    tid = r.get("theme_id") or r.get("focus_theme_id")
                    if tid:
                        theme_ids.add(str(tid))
            except Exception:
                # Table shape can drift across migrations — non-fatal.
                opp_rows = []

        if opp_rows:
            self._write_rows(db_dir / "serenity_opportunities.jsonl", opp_rows)
        if theme_ids:
            self._export_by_id(db_dir, "serenity_themes", "id", theme_ids)
            self._export_by_id(db_dir, "serenity_evidence", "theme_id", theme_ids)
            self._export_by_id(db_dir, "serenity_claims", "theme_id", theme_ids)
        self._copy_artifacts(bundle_dir, ["proposals"])

    def _export_by_id(self, db_dir: Path, table: str, column: str, ids: set[str]) -> None:
        """Emit `SELECT * FROM {table} WHERE {column} IN (...)` as a JSONL file.

        Table/column names are trusted (not caller-controlled — always literals
        from the surrounding _export_proposal). ids are parametrised so the IN
        list can't smuggle SQL. Silent no-op on schema drift (missing column /
        missing table) — never fail the export because a serenity sub-table
        moved under a migration.
        """
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        sql = f"SELECT * FROM {table} WHERE {column} IN ({placeholders})"
        try:
            rows = get_db().execute(sql, tuple(ids)).fetchall()
        except Exception:
            return
        if not rows:
            return
        self._write_rows(db_dir / f"{table}.jsonl", [dict(r) for r in rows])

    @staticmethod
    def _write_rows(path: Path, rows) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _export_runtime(self, bundle_dir: Path) -> None:
        """Export open orders, positions, gateway state."""
        self._export_db_tables(bundle_dir, [
            "ps_orders", "ps_fills", "ps_positions", "ps_gateway_sessions",
            "ps_order_intents", "ps_execution_intents",
        ])

    def _export_full(self, bundle_dir: Path) -> None:
        """Export everything except secrets."""
        self._export_config(bundle_dir)
        # Use SQLite's online backup API so a live WAL database is exported as
        # one consistent snapshot instead of copying only app.db mid-write.
        db_path = self._data_dir / "app.db"
        if db_path.exists():
            (bundle_dir / "db").mkdir(exist_ok=True)
            source = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
            destination = sqlite3.connect(bundle_dir / "db" / "app.db")
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
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
                cursor = db.execute(sql, params)
                first = cursor.fetchone()
            except Exception:
                continue
            if first is None:
                continue
            with open(db_dir / f"{table}.jsonl", "w", encoding="utf-8") as f:
                f.write(json.dumps(dict(first), ensure_ascii=False) + "\n")
                for row in cursor:
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
                    shutil.copytree(item, artifacts_dst / item.name, dirs_exist_ok=True, ignore=_secret_ignore)
        else:
            for cat in categories:
                src = artifacts_src / cat
                if src.exists():
                    shutil.copytree(src, artifacts_dst / cat, dirs_exist_ok=True, ignore=_secret_ignore)

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
