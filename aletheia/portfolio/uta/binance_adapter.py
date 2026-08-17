"""Binance Testnet adapter — IBroker implementation wrapping BinanceTestnetClient."""

import logging

from aletheia.portfolio.uta.broker_protocol import (
    AccountInfo,
    BrokerPosition,
    parse_exchange_leverage,
)

logger = logging.getLogger("aletheia.uta.binance")


class BinanceTestnetAdapter:
    """IBroker adapter for Binance Futures Testnet (read-only queries)."""

    def __init__(self):
        from aletheia.config.secrets import get_secret

        if not get_secret("binance.testnet.api_key") or not get_secret("binance.testnet.api_secret"):
            raise RuntimeError("Binance testnet credentials not configured")

        from aletheia.exchanges.binance.client import BinanceTestnetClient
        self._client = BinanceTestnetClient()

    @property
    def broker_id(self) -> str:
        return "binance_testnet"

    @property
    def display_name(self) -> str:
        return "Binance Testnet"

    async def get_account(self) -> AccountInfo:
        balance = await self._client.fetch_balance()

        total_equity = float(balance.get("total", {}).get("USDT", 0) or 0)
        free = float(balance.get("free", {}).get("USDT", 0) or 0)
        used = float(balance.get("used", {}).get("USDT", 0) or 0)

        info = balance.get("info", {})
        unrealized = 0.0
        for asset_info in info.get("assets", []) or []:
            if asset_info.get("asset") == "USDT":
                unrealized = float(asset_info.get("unrealizedProfit", 0) or 0)
                break

        breakdown = []
        if total_equity > 0:
            breakdown = [{"coin": "USDT", "usdt_value": total_equity, "pct": 100.0}]

        return AccountInfo(
            equity_usdt=total_equity,
            available_usdt=free,
            frozen_usdt=used,
            unrealized_pnl=unrealized,
            asset_breakdown=breakdown,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        raw = await self._client.get_positions()
        result = []
        for p in raw:
            ccxt_symbol = p.get("symbol", "")
            symbol = ccxt_symbol.replace(":USDT", "") if ":USDT" in ccxt_symbol else ccxt_symbol
            leverage, leverage_source = parse_exchange_leverage(p.get("leverage"))
            result.append(BrokerPosition(
                symbol=symbol,
                side=p.get("side", "long"),
                quantity=p.get("quantity", 0),
                entry_price=p.get("entry_price", 0),
                unrealized_pnl=p.get("unrealized_pnl", 0),
                leverage=leverage,
                mark_price=p.get("mark_price", 0),
                position_id=p.get("position_id"),
                account_fingerprint=p.get("account_fingerprint"),
                leverage_source=leverage_source,
            ))
        return result

    async def test_connection(self) -> bool:
        try:
            await self._client.fetch_time()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()
