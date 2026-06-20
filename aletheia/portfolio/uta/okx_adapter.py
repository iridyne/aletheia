"""OKX Demo adapter — IBroker implementation wrapping OKXClient for UTA queries."""

import logging

from aletheia.portfolio.uta.broker_protocol import AccountInfo, BrokerPosition

logger = logging.getLogger("aletheia.uta.okx")


class OkxDemoAdapter:
    """IBroker adapter for OKX Demo (simulated trading, read-only queries)."""

    def __init__(self):
        from aletheia.config.secrets import get_secret

        if (not get_secret("okx.demo.api_key")
                or not get_secret("okx.demo.api_secret")
                or not get_secret("okx.demo.passphrase")):
            raise RuntimeError("OKX demo credentials not configured")

        from aletheia.exchanges.okx.client import OKXClient
        self._client = OKXClient(mode="demo")

    @property
    def broker_id(self) -> str:
        return "okx_demo"

    @property
    def display_name(self) -> str:
        return "OKX Demo"

    async def get_account(self) -> AccountInfo:
        balance = await self._client.fetch_balance()

        total_equity = float(balance.get("total", {}).get("USDT", 0) or 0)
        free = float(balance.get("free", {}).get("USDT", 0) or 0)
        used = float(balance.get("used", {}).get("USDT", 0) or 0)

        info = balance.get("info", {})
        unrealized = 0.0
        for detail in info.get("data", []) or []:
            for acct_detail in detail.get("details", []) or []:
                if acct_detail.get("ccy") == "USDT":
                    unrealized = float(acct_detail.get("upl", 0) or 0)
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
            leverage_raw = p.get("leverage")
            result.append(BrokerPosition(
                symbol=symbol,
                side=p.get("side", "long"),
                quantity=p.get("quantity", 0),
                entry_price=p.get("entry_price", 0),
                unrealized_pnl=p.get("unrealized_pnl", 0),
                leverage=int(leverage_raw) if leverage_raw else 1,
                mark_price=p.get("mark_price", 0),
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
