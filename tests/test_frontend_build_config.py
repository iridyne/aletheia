from pathlib import Path


def test_vite_build_does_not_empty_served_dashboard_dir():
    """Production UI builds must not create an API-only restart window.

    Vite's default `emptyOutDir` deletes dashboard_static before writing the new
    bundle. If the gateway starts in that window, "/" can stay unavailable until
    the next restart. The shipped config keeps old hashed assets while writing
    the new bundle.
    """
    cfg = Path("frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "emptyOutDir: false" in cfg
