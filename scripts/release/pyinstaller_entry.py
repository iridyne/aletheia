"""PyInstaller entrypoint for the self-contained Aletheia runtime/CLI payload."""

from aletheia.main import app


if __name__ == "__main__":
    app()
