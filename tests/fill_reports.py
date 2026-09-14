"""Test-only construction of the Core FillReport boundary."""

from datetime import datetime, timezone

from extensions.exchanges.bitget.client import normalize_private_fill_reports
from aletheia.uta.models import UTAAccountIdentity


DEFAULT_IDENTITY = UTAAccountIdentity(
    account_id="bitget_demo",
    execution_mode="bitget_demo",
    venue="bitget",
    environment="demo",
    profile="classic_v2",
    credential_generation=1,
    adapter_implementation="CCXTUTAAdapter",
    adapter_version="1",
)


def reports(
    rows: list[dict],
    *,
    identity: UTAAccountIdentity = DEFAULT_IDENTITY,
    symbol: str = "BTC/USDT",
    market_type: str = "usdt_futures",
    received_at: datetime | None = None,
):
    return normalize_private_fill_reports(
        rows,
        symbol=symbol,
        market_type=market_type,
        identity=identity,
        received_at=received_at or datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
