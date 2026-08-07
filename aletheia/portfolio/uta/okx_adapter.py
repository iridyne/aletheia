"""OKX Demo adapter — IBroker implementation wrapping OKXClient for UTA queries."""

import logging

from aletheia.portfolio.uta.broker_protocol import (
    AccountInfo,
    BrokerPosition,
    parse_exchange_leverage,
)

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

        # OKX unified account: info.data[0].totalEq is the full-asset USD equity
        # (includes all spot holdings + derivatives equity, priced by OKX).
        # Fallback to balance.total.USDT only if totalEq is unavailable.
        info = balance.get("info", {})
        total_equity = 0.0
        for detail in info.get("data", []) or []:
            total_eq_raw = detail.get("totalEq")
            if total_eq_raw:
                total_equity = float(total_eq_raw)
                break
        if total_equity <= 0:
            total_equity = float(balance.get("total", {}).get("USDT", 0) or 0)

        free = float(balance.get("free", {}).get("USDT", 0) or 0)
        used = float(balance.get("used", {}).get("USDT", 0) or 0)

        # Per-currency breakdown from OKX unified account details
        unrealized = 0.0
        breakdown = []
        for detail in info.get("data", []) or []:
            for acct_detail in detail.get("details", []) or []:
                ccy = (acct_detail.get("ccy") or "").upper()
                eq_val = float(acct_detail.get("eqUsd") or acct_detail.get("eq") or 0)
                upl = float(acct_detail.get("upl") or 0)
                unrealized += upl
                if eq_val > 0 and ccy:
                    breakdown.append({"coin": ccy, "usdt_value": round(eq_val, 4), "pct": 0.0})

        # Compute pct from breakdown
        if breakdown and total_equity > 0:
            for item in breakdown:
                item["pct"] = round(item["usdt_value"] / total_equity * 100, 2)
        elif total_equity > 0 and not breakdown:
            breakdown = [{"coin": "USDT", "usdt_value": total_equity, "pct": 100.0}]

        return AccountInfo(
            equity_usdt=round(total_equity, 4),
            available_usdt=round(free, 4),
            frozen_usdt=round(used, 4),
            unrealized_pnl=round(unrealized, 4),
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
