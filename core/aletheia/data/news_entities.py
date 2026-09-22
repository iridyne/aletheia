"""Deterministic crypto entity matching for collected and historical news."""

from __future__ import annotations

import re


_ENTITY_ALIASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("BTC/USDT", "BTC", ("Bitcoin",)),
    ("ETH/USDT", "ETH", ("Ethereum", "Ether")),
    ("SOL/USDT", "SOL", ("Solana",)),
    ("XRP/USDT", "XRP", ("Ripple",)),
    ("ADA/USDT", "ADA", ("Cardano",)),
    ("DOGE/USDT", "DOGE", ("Dogecoin",)),
    ("BNB/USDT", "BNB", ("Binance Coin",)),
    ("AVAX/USDT", "AVAX", ("Avalanche",)),
    ("DOT/USDT", "DOT", ("Polkadot",)),
    ("MATIC/USDT", "MATIC", ("Polygon",)),
    ("LINK/USDT", "LINK", ("Chainlink",)),
)

_COMPOUND_SUFFIXES = {
    "bitcoin": re.compile(r"^\s+cash\b", re.IGNORECASE),
    "ethereum": re.compile(r"^\s+classic\b", re.IGNORECASE),
}
_RIPPLE_CONTEXT_RE = re.compile(
    r"\b(?:xrp|crypto(?:currency)?|token|blockchain|ledger|stablecoin|sec|labs)\b",
    re.IGNORECASE,
)


def _name_is_entity(text: str, name: str, match: re.Match[str]) -> bool:
    suffix = _COMPOUND_SUFFIXES.get(name.lower())
    if suffix is not None and suffix.search(text[match.end():]):
        return False
    if name.lower() == "ripple":
        context = text[max(0, match.start() - 80):match.end() + 80]
        return _RIPPLE_CONTEXT_RE.search(context) is not None
    return True


def match_crypto_entities(*texts: str | None) -> list[str]:
    """Return canonical symbols named by an exact ticker or asset-name token."""
    text = " ".join(str(value or "") for value in texts)
    entities = []
    for symbol, ticker, names in _ENTITY_ALIASES:
        ticker_pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])"
        )
        matched = ticker_pattern.search(text) is not None
        if not matched:
            for name in names:
                pattern = re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])",
                    re.IGNORECASE,
                )
                if any(_name_is_entity(text, name, match) for match in pattern.finditer(text)):
                    matched = True
                    break
        if matched:
            entities.append(symbol)
    return entities


def news_matches_symbol(symbol: str, *texts: str | None) -> bool:
    """Revalidate a stored news association against its current article text."""
    canonical = symbol if "/" in symbol else f"{symbol}/USDT"
    return canonical.upper() in match_crypto_entities(*texts)
