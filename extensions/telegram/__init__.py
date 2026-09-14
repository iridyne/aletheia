"""Optional Telegram transport; resolves only its own bot token."""

from aletheia.telegram import configure_transport

from .bot import TelegramBot
from .health import health


def register_transport() -> None:
    configure_transport(TelegramBot, health)
