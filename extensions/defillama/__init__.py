"""Optional public on-chain data producer; Core retains persistence and cadence."""


def register_defillama_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_defillama_collector
    from extensions.defillama.collector import DefiLlamaCollector

    configure_defillama_collector(lambda: DefiLlamaCollector().collect())
