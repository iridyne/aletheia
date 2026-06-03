"""Auto-open dashboard in default browser."""

import webbrowser


def open_browser(port: int) -> None:
    url = f"http://127.0.0.1:{port}"
    webbrowser.open(url)
