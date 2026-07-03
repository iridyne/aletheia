"""Export the FastAPI OpenAPI schema for frontend type generation.

This script is intentionally offline: it imports the FastAPI app and writes the
schema without starting the gateway or touching runtime state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_OUTPUT = Path("docs/_generated/openapi.json")


def export_openapi_schema(output: Path) -> dict:
    from aletheia.server.app import app

    schema = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export Aletheia FastAPI OpenAPI schema as JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path. Default: {DEFAULT_OUTPUT}",
    )
    args = parser.parse_args(argv)

    schema = export_openapi_schema(args.output)
    paths = len(schema.get("paths") or {})
    components = schema.get("components") or {}
    schemas = len(components.get("schemas") or {})
    print(f"wrote {args.output} paths={paths} schemas={schemas}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
