"""Optional News producers, bound to the existing Core scheduler."""


def register_news_jobs() -> None:
    from aletheia.agent_runtime.scheduler import configure_news_jobs

    fetch = analyze = prune = None
    try:
        import extensions.news.collector as collector
    except ModuleNotFoundError:
        pass
    else:
        fetch = lambda: collector.fetch_news_feeds()
        prune = lambda days: collector.prune_old_news(days)
    try:
        import extensions.news.analyzer as analyzer
    except ModuleNotFoundError:
        pass
    else:
        analyze = lambda: analyzer.analyze_pending_news(batch_size=3)
    configure_news_jobs(fetch=fetch, analyze=analyze, prune=prune)
