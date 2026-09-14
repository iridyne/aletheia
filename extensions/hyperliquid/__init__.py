"""Optional public Hyperliquid observations; no trading authority."""


def register_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_hyperliquid_collector
    from extensions.hyperliquid.collector import HyperliquidCollector

    configure_hyperliquid_collector(lambda symbols: HyperliquidCollector().collect(symbols))
