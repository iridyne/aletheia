"""Optional Deribit public options context; observation-only."""


def register_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_options_structure_collector
    from extensions.deribit.options_structure import OptionsStructureCollector

    configure_options_structure_collector(lambda: OptionsStructureCollector().collect())
