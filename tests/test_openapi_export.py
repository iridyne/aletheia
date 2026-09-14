import json
import subprocess
import sys
from pathlib import Path


def test_openapi_export_script_writes_schema(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "openapi.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.export_openapi_schema",
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    schema = json.loads(output.read_text(encoding="utf-8"))
    assert schema["openapi"].startswith("3.")
    assert "/api/status" in schema["paths"]
    assert "/api/operator/recommendations" in schema["paths"]
    assert "/api/serenity/status" in schema["paths"]
    assert "/api/news" in schema["paths"]
    assert "/api/prediction/status" in schema["paths"]
    assert "observed_stocks" not in schema["components"]["schemas"]["MarketDataConfigUpdate"]["properties"]
    assert "observed_xstocks" not in schema["components"]["schemas"]["MarketDataConfigUpdate"]["properties"]
    assert "observed_equities" not in schema["components"]["schemas"]["MarketDataConfigUpdate"]["properties"]
    assert "paths=" in result.stdout
