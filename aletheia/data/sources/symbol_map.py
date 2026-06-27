"""Canonical symbol → vendor-specific ID mappings for alt data sources."""

from __future__ import annotations

_CG_SLUG: dict[str, str] = {
    "BTC/USDT": "bitcoin",
    "ETH/USDT": "ethereum",
    "SOL/USDT": "solana",
    "BNB/USDT": "binancecoin",
    "XRP/USDT": "ripple",
    "ADA/USDT": "cardano",
    "AVAX/USDT": "avalanche-2",
    "DOT/USDT": "polkadot",
    "MATIC/USDT": "matic-network",
    "LINK/USDT": "chainlink",
    "DOGE/USDT": "dogecoin",
    "LTC/USDT": "litecoin",
    "UNI/USDT": "uniswap",
    "ATOM/USDT": "cosmos",
    "FIL/USDT": "filecoin",
    "NEAR/USDT": "near",
    "OP/USDT": "optimism",
    "ARB/USDT": "arbitrum",
    "SUI/USDT": "sui",
    "APT/USDT": "aptos",
    "PEPE/USDT": "pepe",
    "WIF/USDT": "dogwifcoin",
    "TRX/USDT": "tron",
    "TON/USDT": "the-open-network",
    "RENDER/USDT": "render-token",
    "INJ/USDT": "injective-protocol",
    "FET/USDT": "fetch-ai",
    "AAVE/USDT": "aave",
    "MKR/USDT": "maker",
    "CRV/USDT": "curve-dao-token",
}


def to_coingecko_id(canonical: str) -> str | None:
    """Map a canonical symbol to its CoinGecko coin ID, or None if unmapped (F6).

    The prior KeyError forced a hard failure for any of the many coins not in the
    static slug map. Returning None lets callers treat it as "CoinGecko can't serve
    this symbol" and degrade gracefully (fall back to another data source) rather
    than raising an unexpected KeyError up the stack.
    """
    from aletheia.data.symbols import to_canonical

    canon = to_canonical(canonical)
    return _CG_SLUG.get(canon)


def to_binance_symbol(canonical: str) -> str:
    """Map canonical symbol to Binance public API format (BTCUSDT)."""
    from aletheia.data.symbols import to_binance

    return to_binance(canonical)
