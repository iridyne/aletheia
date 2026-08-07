"""Bounded, non-restorable data export API routes.

The CSV presets are for analysis and diagnosis. Runtime transfer/recovery uses the
managed BackupService/RestoreService path; these ZIP files are never restore input.
"""

import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
import yaml

router = APIRouter(prefix="/api/export", tags=["export"])

# Ordered list of exportable datasets.
# Each entry: (table_name, csv_filename, preset_membership)
# preset_membership: set of presets that include this dataset by default
_DATASETS: list[tuple[str, str, set[str]]] = [
    ("opportunities",        "opportunities.csv",         {"diagnostic"}),
    ("pi_decisions",         "pi_decisions.csv",          {"diagnostic"}),
    ("trade_proposals",      "trade_proposals.csv",       {"analysis", "diagnostic"}),
    ("risk_decisions",       "risk_decisions.csv",        {"analysis", "diagnostic"}),
    ("orders",               "orders.csv",                {"analysis", "diagnostic"}),
    ("fills",                "fills.csv",                 {"analysis", "diagnostic"}),
    ("positions",            "positions.csv",             {"analysis", "diagnostic"}),
    ("capital_events",       "capital_events.csv",        {"analysis", "diagnostic"}),
    ("paper_account_events", "paper_account_events.csv",  {"analysis", "diagnostic"}),
    ("agent_runs",           "agent_runs.csv",            {"diagnostic"}),
    ("agent_run_steps",      "agent_run_steps.csv",       {"diagnostic"}),
    ("agent_tool_calls",     "agent_tool_calls.csv",      {"diagnostic"}),
    ("audit_events",         "audit_events.csv",          {"diagnostic"}),
]

_ALL_DATASET_NAMES = [d[0] for d in _DATASETS]

# Map table_name -> (csv_filename, default_presets)
_DATASET_MAP = {d[0]: (d[1], d[2]) for d in _DATASETS}

_DATASET_CLASSIFICATIONS = {
    "opportunities": "required_durable",
    "pi_decisions": "required_durable",
    "trade_proposals": "required_durable",
    "risk_decisions": "required_durable",
    "orders": "required_durable",
    "fills": "required_durable",
    "positions": "required_durable",
    "capital_events": "required_durable",
    "paper_account_events": "required_durable",
    "agent_runs": "optional_history",
    "agent_run_steps": "optional_history",
    "agent_tool_calls": "optional_history",
    "audit_events": "optional_history",
}

_PRESETS = {
    "analysis": {
        "version": 1,
        "purpose": "Selected trading tables for external analysis; not a runtime backup.",
        "restore_supported": False,
    },
    "diagnostic": {
        "version": 1,
        "purpose": (
            "Bounded private diagnostic snapshot with selected causal, Agent trace, "
            "audit, and redacted configuration data; not dependency-closed."
        ),
        "restore_supported": False,
    },
}

_REMOVED_PRESETS = {
    "migration": {
        "replacement": "diagnostic",
        "reason": (
            "The former CSV bundle was not dependency-closed or restorable and cannot "
            "honestly represent instance transfer."
        ),
        "backup_api": "/api/persistence/backups",
    }
}

_EXCLUDED_STATE = [
    {
        "classification": "required_durable",
        "state": "all unlisted SQLite tables and non-SQLite authority files",
        "reason": "The diagnostic preset is intentionally not dependency-closed; use a managed backup.",
    },
    {
        "classification": "optional_history",
        "state": "unlisted or already-retained telemetry, JSONL history, and artifacts",
        "reason": "Absence from this archive does not prove the source history never existed.",
    },
    {
        "classification": "rebuildable_cache",
        "state": "market caches, derived indexes, and other rebuildable projections",
        "reason": "These sources are outside the bounded CSV catalog.",
    },
    {
        "classification": "ephemeral_lock",
        "state": "PID files, sockets, lock files, SQLite WAL/SHM files, and process ownership",
        "reason": "Ephemeral runtime authority must never transfer through a diagnostic archive.",
    },
    {
        "classification": "secret_excluded",
        "state": "credentials, secret files, and unredacted configuration values",
        "reason": "Secrets are never included; config_backup.yaml is a redacted diagnostic copy only.",
    },
]

_EXPORT_ORDER_BY = {
    "agent_runs": "started_at ASC, rowid ASC",
    "agent_run_steps": "run_id ASC, created_at ASC, rowid ASC",
    "agent_tool_calls": "run_id ASC, created_at ASC, rowid ASC",
}

_AGENT_TRACE_TABLES = frozenset({"agent_runs", "agent_run_steps", "agent_tool_calls"})

_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:api_key|token|secret|password|passphrase|credentials(?:_path)?|private_key|secret_key)$"
)


def _count_rows(table: str, db) -> int:
    try:
        row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        return row[0] if row else 0
    except Exception:
        return -1


def _write_table_csv(table: str, db, zf: zipfile.ZipFile, filename: str) -> int:
    """Stream one table into a ZIP member without materializing all rows."""
    order_by = _EXPORT_ORDER_BY.get(table)
    sql = f"SELECT * FROM {table}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    cursor = db.execute(sql)  # noqa: S608
    cols = [desc[0] for desc in cursor.description]
    row_count = 0
    with zf.open(filename, mode="w") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
            writer = csv.writer(text)
            writer.writerow(cols)
            for row in cursor:
                writer.writerow(row)
                row_count += 1
    return row_count


def _zip_member_record(
    zf: zipfile.ZipFile,
    filename: str,
    *,
    classification: str,
    row_count: int | None = None,
    table: str | None = None,
) -> dict:
    """Hash one completed member in bounded chunks for the archive manifest."""
    digest = hashlib.sha256()
    with zf.open(filename, mode="r") as member:
        while chunk := member.read(1024 * 1024):
            digest.update(chunk)
    record = {
        "filename": filename,
        "bytes": zf.getinfo(filename).file_size,
        "sha256": digest.hexdigest(),
        "classification": classification,
    }
    if table is not None:
        record["table"] = table
    if row_count is not None:
        record["row_count"] = row_count
    return record


def _redact_proxy_url(value: object) -> str:
    from urllib.parse import urlsplit, urlunsplit

    raw = str(value or "")
    try:
        parsed = urlsplit(raw)
        if not parsed.username and not parsed.password:
            return raw
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, f"<REDACTED>@{host}", parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "<REDACTED>"


def _redact_config_value(value: object, *, key: str = "") -> object:
    if _SECRET_KEY_RE.search(key):
        return "<REDACTED>"
    if key.lower() == "proxy_url":
        return _redact_proxy_url(value)
    if isinstance(value, dict):
        return {
            str(child_key): _redact_config_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_config_value(item) for item in value]
    return value


def _redacted_config_yaml() -> str:
    """Return parsed config with credential fields removed; parse failure omits it."""
    from aletheia.config.loader import CONFIG_PATH

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "# config.yaml not found\n"
    try:
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ValueError("config root must be a mapping")
        redacted = _redact_config_value(loaded)
        rendered = yaml.safe_dump(redacted, sort_keys=False, allow_unicode=True)
    except Exception:
        return "# config.yaml omitted: unable to parse and redact safely\n"
    return f"# Secrets redacted — diagnostic copy, not restore input\n{rendered}"


@router.get("/catalog")
def get_export_catalog():
    """Return honest preset semantics, row counts, and dataset classifications."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    items = []
    for table, filename, presets in _DATASETS:
        items.append({
            "table": table,
            "filename": filename,
            "presets": sorted(presets),
            "row_count": _count_rows(table, db),
            "classification": _DATASET_CLASSIFICATIONS[table],
        })
    return {
        "presets": _PRESETS,
        "removed_presets": _REMOVED_PRESETS,
        "datasets": items,
        "managed_recovery": {
            "backup_api": "/api/persistence/backups",
            "verify_api": "/api/persistence/backups/verify",
            "restore_service": "aletheia.backup.restore.RestoreService",
        },
    }


class ExportRequest(BaseModel):
    preset: Optional[str] = None          # "analysis" | "diagnostic" | None (custom)
    include_datasets: Optional[list[str]] = None  # custom override
    include_config: bool = False


def _resolve_datasets(req: ExportRequest) -> list[str]:
    """Return ordered list of table names to include in this export."""
    if req.preset == "migration":
        raise HTTPException(
            400,
            {
                "code": "migration_preset_removed",
                **_REMOVED_PRESETS["migration"],
            },
        )
    if req.preset is not None and req.include_datasets is not None:
        raise HTTPException(
            400,
            {
                "code": "ambiguous_export_request",
                "reason": "Specify either preset or include_datasets, not both.",
            },
        )
    if req.include_datasets is not None:
        if not req.include_datasets:
            raise HTTPException(400, "include_datasets must not be empty")
        unknown = set(req.include_datasets) - set(_ALL_DATASET_NAMES)
        if unknown:
            raise HTTPException(400, f"Unknown datasets: {sorted(unknown)}")
        requested = set(req.include_datasets)
        selected_trace = requested & _AGENT_TRACE_TABLES
        if selected_trace and selected_trace != _AGENT_TRACE_TABLES:
            raise HTTPException(
                400,
                {
                    "code": "partial_agent_trace",
                    "required_together": sorted(_AGENT_TRACE_TABLES),
                    "reason": (
                        "Agent reasoning steps and tool calls are one logical trace; "
                        "export all physical sources together."
                    ),
                },
            )
        # Preserve canonical order
        return [t for t in _ALL_DATASET_NAMES if t in requested]

    if req.preset in _PRESETS:
        tables = [t for t, _, presets in _DATASETS if req.preset in presets]
        return tables

    raise HTTPException(400, "Specify 'preset' (analysis|diagnostic) or 'include_datasets'")


def _build_zip(tables: list[str], include_config: bool, preset_name: str) -> Path:
    """Build a disk-backed archive and return its temporary path."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    temp = tempfile.NamedTemporaryFile(
        prefix="aletheia_export_",
        suffix=".zip",
        delete=False,
    )
    path = Path(temp.name)
    temp.close()
    try:
        with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            file_records = []
            for table in tables:
                filename = _DATASET_MAP[table][0]
                row_count = _write_table_csv(table, db, zf, filename)
                file_records.append(_zip_member_record(
                    zf,
                    filename,
                    classification=_DATASET_CLASSIFICATIONS[table],
                    row_count=row_count,
                    table=table,
                ))

            if include_config:
                config_filename = "config_backup.yaml"
                zf.writestr(config_filename, _redacted_config_yaml())
                file_records.append(_zip_member_record(
                    zf,
                    config_filename,
                    classification="secret_excluded",
                ))

            from aletheia import __version__
            from aletheia.storage.migrations import SCHEMA_VERSION

            preset = _PRESETS.get(preset_name, {
                "version": 1,
                "purpose": "Caller-selected bounded dataset subset; not a runtime backup.",
                "restore_supported": False,
            })
            manifest = {
                "manifest_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "producer": {
                    "application": "aletheia",
                    "application_version": __version__,
                    "schema_version": SCHEMA_VERSION,
                },
                "preset": {
                    "name": preset_name,
                    "version": preset["version"],
                    "purpose": preset["purpose"],
                },
                "consistency": "best_effort_table_by_table",
                "restore": {
                    "supported": False,
                    "reason": "CSV diagnostic exports are not dependency-closed restore inputs.",
                    "backup_api": "/api/persistence/backups",
                    "verify_api": "/api/persistence/backups/verify",
                    "restore_service": "aletheia.backup.restore.RestoreService",
                },
                "agent_trace": {
                    "sources": [
                        table for table in ("agent_runs", "agent_run_steps", "agent_tool_calls")
                        if table in tables
                    ],
                    "ordering": (
                        "Within each run, merge agent_run_steps and agent_tool_calls by created_at; "
                        "CSV row order is the intra-source tiebreaker and step precedes tool_call "
                        "when timestamps are equal."
                    ),
                    "retention": (
                        "Agent trace is best-effort telemetry; absent rows do not prove that no "
                        "reasoning or tool action occurred."
                    ),
                    "privacy": "Agent input/output fields may contain private runtime data; do not share blindly.",
                },
                "files": file_records,
                "excluded_state": _EXCLUDED_STATE,
            }
            zf.writestr(
                "export_manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
            )
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.post("/download")
async def download_export(req: ExportRequest):
    """Generate and stream a ZIP archive of the requested datasets."""
    tables = _resolve_datasets(req)
    if not tables and not req.include_config:
        raise HTTPException(400, "Nothing to export")

    include_config = req.include_config or req.preset == "diagnostic"

    from starlette.concurrency import run_in_threadpool
    label = req.preset or "custom"
    zip_path = await run_in_threadpool(_build_zip, tables, include_config, label)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"aletheia_{label}_{ts}.zip"

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )
