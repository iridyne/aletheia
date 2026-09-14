"""Optional Coinalyze adapter; only its own credential reference is resolved."""


def register_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_coinalyze_collector
    from extensions.coinalyze.collector import CoinalyzeCollector, is_configured

    configure_coinalyze_collector(
        lambda symbols: CoinalyzeCollector().collect(symbols), is_configured,
    )
