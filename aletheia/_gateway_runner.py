"""Background gateway runner script — spawned by `aletheia gateway start`."""

import sys

from aletheia.config.loader import bootstrap
from aletheia.gateway.pidfile import write_pid
from aletheia.gateway.browser import open_browser


def main():
    no_browser = "--no-browser" in sys.argv
    cfg = bootstrap()
    write_pid()
    if not no_browser and cfg.app.auto_open_browser:
        open_browser(cfg.app.dashboard_port)
    import uvicorn
    uvicorn.run(
        "aletheia.server.app:app",
        host=cfg.app.bind_host,
        port=cfg.app.dashboard_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
