import json
import os
from pathlib import Path
import subprocess


def test_performance_gate_uses_generated_sidecars_and_rejects_missing(tmp_path):
    assets = tmp_path / "assets"
    manifest_dir = tmp_path / ".vite"
    assets.mkdir()
    manifest_dir.mkdir()
    html = tmp_path / "index.html"
    script = assets / "index.js"
    html.write_text('<div id="boot-skeleton"></div>' + " " * 256)
    script.write_text("const stable = true;" + " " * 256)
    (manifest_dir / "manifest.json").write_text(json.dumps({
        "index.html": {"file": "assets/index.js", "isEntry": True},
    }))
    for path, gzip_size, brotli_size in ((html, 17, 13), (script, 19, 11)):
        Path(f"{path}.gz").write_bytes(b"g" * gzip_size)
        Path(f"{path}.br").write_bytes(b"b" * brotli_size)

    env = {**os.environ, "VITE_BUILD_OUT_DIR": str(tmp_path)}
    command = ["node", "scripts/check-performance.mjs"]
    result = subprocess.run(command, cwd="frontend", env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    performance = json.loads((tmp_path / "performance.json").read_text())
    assert performance["critical_js"]["gzip"] == 19
    assert performance["critical_js"]["br"] == 11
    assert performance["total_static"]["html_gzip"] == 17
    assert performance["total_static"]["html_br"] == 13

    Path(f"{script}.br").unlink()
    missing = subprocess.run(command, cwd="frontend", env=env, capture_output=True, text=True)
    assert missing.returncode == 1
    assert "missing compression sidecar" in missing.stderr

    script.write_text("tiny")
    Path(f"{script}.gz").unlink()
    tiny = subprocess.run(command, cwd="frontend", env=env, capture_output=True, text=True)
    assert tiny.returncode == 0, tiny.stderr
    performance = json.loads((tmp_path / "performance.json").read_text())
    assert performance["critical_js"]["gzip"] == 4
    assert performance["critical_js"]["br"] == 4
