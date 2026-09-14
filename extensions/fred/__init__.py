"""Bind FRED acquisition to the existing macro collector; no new lifecycle."""


def register_collector() -> None:
    from extensions.macro.collector import configure_fred_collector
    from extensions.fred.collector import collect_macro, collect_research, is_configured

    configure_fred_collector(collect_macro, collect_research, is_configured)
