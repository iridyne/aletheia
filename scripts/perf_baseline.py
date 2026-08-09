#!/usr/bin/env python3
"""aletheia perf_baseline — read-only performance baseline snapshot.

Measures the five target analytics endpoints (p50/p95 latency over 10
requests each), records database size + high-growth table row counts,
and captures first-screen static resource bytes. Safe to run repeatedly;
performs NO writes against the database, the filesystem, or any
configuration.

Usage:
    python3 scripts/perf_baseline.py              # print + write JSON
    python3 scripts/perf_baseline.py --json        # machine-readable only
    python3 scripts/perf_baseline.py --n 10        # requests per endpoint
    python3 scripts/perf_baseline.py --port 8787   # dashboard port
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ── Target endpoints (relative to the dashboard origin) ────────────────────
_TARGET_ENDPOINTS = [
    "/api/analytics/opportunity-funnel",
    "/api/analytics/equity-curve",
    "/api/analytics/pipeline-funnel",
    "/api/analytics/operator-decisions",
    "/api/analytics/opportunity-funnel/current-snapshot",
]

# ── High-growth tables to count (must match schema in migrations.py) ───────
_HIGH_GROWTH_TABLES = [
    "filter_candidate_outcomes",
    "agent_shell_heartbeats",
    "filter_decision_batches",
    "opportunities",
    "audit_events",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _data_dir() -> Path:
    """Resolve the active runtime data dir without importing the full config
    stack (this script is read-only and must not trigger config side effects)."""
    override = os.environ.get("ALETHEIA_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".aletheia").resolve()


def _db_path() -> Path:
    return _data_dir() / "app.db"


def _db_size_bytes() -> int | None:
    p = _db_path()
    if not p.exists():
        return None
    return p.stat().st_size


def _table_row_counts() -> dict[str, int | None]:
    """Read row counts for the high-growth tables. Missing tables report None
    rather than raising — the baseline must run against any schema version."""
    import sqlite3

    p = _db_path()
    if not p.exists():
        return {t: None for t in _HIGH_GROWTH_TABLES}
    out: dict[str, int | None] = {}
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, check_same_thread=False)
        try:
            for t in _HIGH_GROWTH_TABLES:
                try:
                    out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                except Exception:
                    out[t] = None
        finally:
            conn.close()
    except Exception:
        return {t: None for t in _HIGH_GROWTH_TABLES}
    return out


def _measure_endpoint(origin: str, path: str, n: int) -> dict:
    """Time GET requests to one endpoint. Returns p50/p95 in ms plus basic
    availability stats. Never raises — failures are reported as zeros so a
    single down endpoint doesn't sink the whole baseline."""
    latencies_ms: list[float] = []
    errors = 0
    url = origin.rstrip("/") + path
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                resp.read()
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        except Exception:
            errors += 1
    if not latencies_ms:
        return {
            "url": url,
            "requests": n,
            "success": 0,
            "errors": errors,
            "p50_ms": None,
            "p95_ms": None,
            "mean_ms": None,
        }
    latencies_ms.sort()
    return {
        "url": url,
        "requests": n,
        "success": len(latencies_ms),
        "errors": errors,
        "p50_ms": round(statistics.median(latencies_ms), 2),
        "p95_ms": round(latencies_ms[int(len(latencies_ms) * 0.95)], 2),
        "mean_ms": round(statistics.mean(latencies_ms), 2),
    }


def _measure_static_bytes(origin: str) -> dict:
    """Fetch the dashboard index.html and sum the bytes of referenced JS/CSS
    assets. If the server is unreachable or the dashboard is not built, report
    what we can without raising."""
    result: dict = {"origin": origin, "index_bytes": None, "asset_bytes": None, "total_bytes": None, "assets": []}
    try:
        req = urllib.request.Request(origin + "/", method="GET")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            html = resp.read()
        result["index_bytes"] = len(html)
        html_text = html.decode("utf-8", errors="replace")
        # Parse <script src=...> and <link href=...> for bundled assets.
        import re
        refs: list[str] = []
        for tag in re.findall(r'(?:src|href)="([^"]+)"', html_text):
            if tag.startswith("http"):
                continue
            if tag.endswith(".js") or tag.endswith(".css"):
                refs.append(tag)
        total = len(html)
        for ref in refs:
            url = origin.rstrip("/") + ("/" + ref if not ref.startswith("/") else ref)
            try:
                r = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(r, timeout=10.0) as resp:
                    b = resp.read()
                total += len(b)
                result["assets"].append({"url": ref, "bytes": len(b)})
            except Exception:
                result["assets"].append({"url": ref, "bytes": None})
        result["asset_bytes"] = total - len(html)
        result["total_bytes"] = total
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _collect(n_per_endpoint: int, port: int) -> dict:
    origin = f"http://127.0.0.1:{port}"
    db_size = _db_size_bytes()
    row_counts = _table_row_counts()

    endpoint_results: list[dict] = []
    for path in _TARGET_ENDPOINTS:
        endpoint_results.append(_measure_endpoint(origin, path, n_per_endpoint))

    static = _measure_static_bytes(origin)

    return {
        "generated_at": _now(),
        "origin": origin,
        "requests_per_endpoint": n_per_endpoint,
        "database": {
            "path": str(_db_path()),
            "size_bytes": db_size,
            "size_mb": round(db_size / (1024 * 1024), 1) if db_size is not None else None,
            "row_counts": row_counts,
        },
        "endpoints": endpoint_results,
        "static_resources": static,
    }


def _render_human(rep: dict) -> str:
    out = []
    out.append("=" * 64)
    out.append("aletheia 性能基线 (只读)")
    out.append("生成时间(UTC): %s" % rep.get("generated_at"))
    out.append("采样/端点: %d" % rep.get("requests_per_endpoint", 0))
    out.append("=" * 64)

    db = rep.get("database", {})
    out.append("")
    out.append("[数据库]")
    out.append("  路径: %s" % db.get("path"))
    out.append("  大小: %s MB" % db.get("size_mb"))
    out.append("  高增长表行数:")
    for t, c in (db.get("row_counts") or {}).items():
        out.append("    %s: %s" % (t, f"{c:,}" if c is not None else "n/a"))

    out.append("")
    out.append("[端点延迟]")
    for ep in rep.get("endpoints", []):
        out.append("  %s" % ep.get("url"))
        out.append(
            "    p50=%s ms | p95=%s ms | mean=%s ms | ok=%d/%d"
            % (
                ep.get("p50_ms"),
                ep.get("p95_ms"),
                ep.get("mean_ms"),
                ep.get("success", 0),
                ep.get("requests", 0),
            )
        )

    st = rep.get("static_resources", {})
    out.append("")
    out.append("[首屏静态资源]")
    out.append("  index.html: %s bytes" % st.get("index_bytes"))
    out.append("  资源合计: %s bytes" % st.get("asset_bytes"))
    out.append("  总传输: %s bytes" % st.get("total_bytes"))
    for a in st.get("assets") or []:
        out.append("    %s: %s bytes" % (a.get("url"), a.get("bytes")))

    out.append("=" * 64)
    return "\n".join(out)


def main() -> int:
    as_json = "--json" in sys.argv[1:]
    n = 10
    port = 8787
    # Minimal argv parsing (avoid argparse overhead for a tiny script).
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--n" and i + 1 < len(argv):
            n = int(argv[i + 1])
            i += 2
        elif argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 2
        else:
            i += 1

    rep = _collect(n_per_endpoint=n, port=port)

    # Write JSON report to data/reports/perf/.
    data_dir = _data_dir()
    perf_dir = data_dir / "reports" / "perf"
    perf_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = perf_dir / f"baseline-{date_tag}.json"
    # If multiple runs per day, disambiguate with a timestamp suffix.
    if out_path.exists():
        time_tag = datetime.now(timezone.utc).strftime("%H%M%S")
        out_path = perf_dir / f"baseline-{date_tag}-{time_tag}.json"
    out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2))

    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(_render_human(rep))
        print(f"\n报告已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
