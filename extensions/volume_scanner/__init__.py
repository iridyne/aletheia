"""Optional public volume ranking; Core owns cadence and persistence."""


def register_collector() -> None:
    from aletheia.agent_runtime.scheduler import configure_volume_scanner_collector
    from extensions.volume_scanner.collector import VolumeScannerCollector

    configure_volume_scanner_collector(
        lambda symbols, top_n: VolumeScannerCollector(top_n=top_n).collect(symbols),
    )
