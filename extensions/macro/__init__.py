"""Optional macro acquisition; Core retains cadence and persistence."""


def register_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_macro_collector
    from extensions.macro.collector import MacroCollector

    configure_macro_collector(lambda symbols: MacroCollector().collect(symbols))
