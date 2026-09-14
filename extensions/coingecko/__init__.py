"""Optional public CoinGecko enrichment."""


def register_top_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_coingecko_top_collector
    from extensions.coingecko.top import CoinGeckoTopCollector

    configure_coingecko_top_collector(lambda top_n: CoinGeckoTopCollector().collect(top_n=top_n))
