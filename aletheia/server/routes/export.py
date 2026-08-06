"""Data export API routes.

Two export presets:
  analysis  — trading data as CSVs, suitable for external analysis
  migration — full dataset + redacted config.yaml, for instance-to-instance transfer
"""

import csv
import io
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

router = APIRouter(prefix="/api/export", tags=["export"])

# Ordered list of exportable datasets.
# Each entry: (table_name, csv_filename, preset_membership)
# preset_membership: set of presets that include this dataset by default
_DATASETS: list[tuple[str, str, set[str]]] = [
    ("trade_proposals",      "trade_proposals.csv",      {"analysis", "migration"}),
    ("risk_decisions",       "risk_decisions.csv",        {"analysis", "migration"}),
    ("orders",               "orders.csv",                {"analysis", "migration"}),
    ("fills",                "fills.csv",                 {"analysis", "migration"}),
    ("positions",            "positions.csv",             {"analysis", "migration"}),
    ("capital_events",       "capital_events.csv",        {"analysis", "migration"}),
    ("paper_account_events", "paper_account_events.csv",  {"analysis", "migration"}),
    ("agent_runs",           "agent_runs.csv",            {"migration"}),
    ("agent_run_steps",      "agent_run_steps.csv",       {"migration"}),
    ("audit_events",         "audit_events.csv",          {"migration"}),
]

_ALL_DATASET_NAMES = [d[0] for d in _DATASETS]

# Map table_name -> (csv_filename, default_presets)
_DATASET_MAP = {d[0]: (d[1], d[2]) for d in _DATASETS}


def _count_rows(table: str, db) -> int:
    try:
        row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        return row[0] if row else 0
    except Exception:
        return -1


def _write_table_csv(table: str, db, zf: zipfile.ZipFile, filename: str) -> None:
    """Stream one table into a ZIP member without materializing all rows."""
    cursor = db.execute(f"SELECT * FROM {table}")  # noqa: S608
    cols = [desc[0] for desc in cursor.description]
    with zf.open(filename, mode="w") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
            writer = csv.writer(text)
            writer.writerow(cols)
            for row in cursor:
                writer.writerow(row)


def _redacted_config_yaml() -> str:
    """Return config.yaml content with secrets replaced by placeholders."""
    from aletheia.config.loader import CONFIG_PATH

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "# config.yaml not found\n"

    # Redact any YAML key whose name ends with a secret-bearing word.
    # Uses MULTILINE so ^ anchors to each line, preventing partial matches like
    # `bot_token:` being mangled into `bot_<REDACTED>`.
    # Pattern: optional indentation + optional prefix + secret keyword + colon + value.
    redacted = re.sub(
        r'(?im)^(\s*\w*(?:api_key|token|secret|password|credentials(?:_path)?)\s*:\s*)(.+)$',
        r'\1<REDACTED>',
        text,
    )
    return f"# Secrets redacted — fill in before use\n{redacted}"


@router.get("/catalog")
def get_export_catalog():
    """Return row counts and preset membership for all exportable datasets."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    items = []
    for table, filename, presets in _DATASETS:
        items.append({
            "table": table,
            "filename": filename,
            "presets": sorted(presets),
            "row_count": _count_rows(table, db),
        })
    return {"datasets": items}


class ExportRequest(BaseModel):
    preset: Optional[str] = None          # "analysis" | "migration" | None (custom)
    include_datasets: Optional[list[str]] = None  # custom override
    include_config: bool = False


def _resolve_datasets(req: ExportRequest) -> list[str]:
    """Return ordered list of table names to include in this export."""
    if req.include_datasets is not None:
        if not req.include_datasets:
            raise HTTPException(400, "include_datasets must not be empty")
        unknown = set(req.include_datasets) - set(_ALL_DATASET_NAMES)
        if unknown:
            raise HTTPException(400, f"Unknown datasets: {sorted(unknown)}")
        # Preserve canonical order
        return [t for t in _ALL_DATASET_NAMES if t in set(req.include_datasets)]

    if req.preset in ("analysis", "migration"):
        tables = [t for t, _, presets in _DATASETS if req.preset in presets]
        return tables

    raise HTTPException(400, "Specify 'preset' (analysis|migration) or 'include_datasets'")


def _build_zip(tables: list[str], include_config: bool) -> Path:
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
            for table in tables:
                filename = _DATASET_MAP[table][0]
                _write_table_csv(table, db, zf, filename)

            if include_config:
                zf.writestr("config_backup.yaml", _redacted_config_yaml())
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

    include_config = req.include_config or req.preset == "migration"

    from starlette.concurrency import run_in_threadpool
    zip_path = await run_in_threadpool(_build_zip, tables, include_config)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = req.preset or "custom"
    filename = f"aletheia_{label}_{ts}.zip"

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )
