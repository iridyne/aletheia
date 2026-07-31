import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bitcoin and SOL rally while Ethereum lags", ["BTC/USDT", "ETH/USDT", "SOL/USDT"]),
        ("A new solution improves adoption in Canada", []),
        ("Polygon upgrade and DOT unlocks", ["DOT/USDT", "MATIC/USDT"]),
        ("dot link sol ada are ordinary lowercase words", []),
        ("$BTC, BTC/USDT and BTC-USDT", ["BTC/USDT"]),
        (
            "bitcoin ETHEREUM solana cardano polkadot chainlink",
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "DOT/USDT", "LINK/USDT"],
        ),
        ("Bitcoin Cash and Ethereum Classic rallied", []),
        ("A ripple spread across global bond markets", []),
        ("Ripple XRP token demand increased", ["XRP/USDT"]),
    ],
)
def test_news_entity_matcher_requires_exact_ticker_or_asset_name(text, expected):
    from aletheia.data.news_entities import match_crypto_entities

    assert match_crypto_entities(text) == expected


def test_news_publication_time_does_not_invent_or_accept_future_time():
    from aletheia.data.news_collector import _parse_time

    now = datetime.now(timezone.utc)
    assert _parse_time({}, now=now) == ""
    assert _parse_time({"published_parsed": (0, 1, 1, 0, 0, 0)}, now=now) == ""
    assert _parse_time(
        {"published_parsed": (now + timedelta(hours=1)).timetuple()}, now=now
    ) == ""
    assert _parse_time(
        {"published_parsed": (now - timedelta(minutes=1)).timetuple()}, now=now
    ) == (now - timedelta(minutes=1)).replace(microsecond=0).isoformat()


def test_batch_news_revalidates_historical_matches_and_requires_analyzed_relevance(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.storage.repositories import batch_news
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = [
        ("false-positive", "New solution improves adoption", "SOL/USDT", "analyzed", 0.99),
        ("not-analyzed", "Solana upgrade ships", "SOL/USDT", "new", 0.99),
        ("low-relevance", "SOL market brief", "SOL/USDT", "analyzed", 0.59),
        ("exact", "Solana network demand rises", "SOL/USDT", "analyzed", 0.60),
    ]
    db.executemany(
        "INSERT INTO news_items "
        "(id, title, source, summary, content, published_at, symbols_mentioned, status, relevance_score) "
        "VALUES (?, ?, 'fixture', '', '', '2026-07-19T00:00:00Z', ?, ?, ?)",
        rows,
    )
    db.execute(
        "INSERT INTO news_items "
        "(id, title, source, summary, content, published_at, symbols_mentioned, status, relevance_score) "
        "VALUES ('future', 'Solana future bulletin', 'fixture', '', '', "
        "'2099-01-01T00:00:00Z', 'SOL/USDT', 'analyzed', 0.99)"
    )
    db.commit()

    result = batch_news(["SOL/USDT"], "2026-07-18T00:00:00Z")

    assert [row["id"] for row in result["SOL/USDT"]] == ["exact"]


@pytest.mark.asyncio
async def test_analyzer_first_failure_schedules_retry_without_marking_analyzed(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items (id, title, source, summary, published_at, status) "
        "VALUES ('news-fail', 'Bitcoin update', 'fixture', '', '2026-07-19T00:00:00Z', 'new')"
    )
    db.commit()

    async def fail_analysis(**_kwargs):
        return None, "provider unavailable"

    monkeypatch.setattr(news_analyzer, "llm_structured", fail_analysis)

    assert await news_analyzer.analyze_pending_news(batch_size=1) == 0
    row = db.execute(
        "SELECT status, analyzed_at FROM news_items WHERE id='news-fail'"
    ).fetchone()
    assert row["status"] == "analysis_retry"
    assert row["analyzed_at"] is not None


@pytest.mark.asyncio
async def test_analyzer_second_due_failure_becomes_terminal_failed(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items "
        "(id, title, source, summary, published_at, status, analyzed_at) "
        "VALUES ('news-retry', 'Bitcoin update', 'fixture', '', "
        "'2026-07-19T00:00:00Z', 'analysis_retry', '2020-01-01T00:00:00Z')"
    )
    db.commit()

    async def invalid_schema(**_kwargs):
        return ["not", "an", "object"], None

    monkeypatch.setattr(news_analyzer, "llm_structured", invalid_schema)

    assert await news_analyzer.analyze_pending_news(batch_size=1) == 0
    row = db.execute(
        "SELECT status, analyzed_at FROM news_items WHERE id='news-retry'"
    ).fetchone()
    assert row["status"] == "analysis_failed"
    assert row["analyzed_at"] != "2020-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_analyzer_timeout_records_first_retry_attempt(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items (id, title, source, summary, published_at, status) "
        "VALUES ('news-timeout', 'Bitcoin timeout', 'fixture', '', "
        "'2026-07-19T00:00:00Z', 'new')"
    )
    db.commit()

    async def hang(**_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(news_analyzer, "llm_structured", hang)
    monkeypatch.setattr(news_analyzer, "_ANALYSIS_ITEM_TIMEOUT_SEC", 0.01)

    assert await news_analyzer.analyze_pending_news(batch_size=1) == 0
    row = db.execute(
        "SELECT status, analyzed_at FROM news_items WHERE id='news-timeout'"
    ).fetchone()
    assert row["status"] == "analysis_retry"
    assert row["analyzed_at"] is not None


@pytest.mark.asyncio
async def test_analyzer_retry_waits_thirty_minutes(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.executemany(
        "INSERT INTO news_items "
        "(id, title, source, summary, published_at, status, analyzed_at) "
        "VALUES (?, ?, 'fixture', '', ?, ?, ?)",
        [
            (
                "retry-not-due", "Bitcoin retry", "2026-07-19T00:00:00Z",
                "analysis_retry", "2999-01-01T00:00:00Z",
            ),
            (
                "new-due", "Ethereum new", "2026-07-19T00:01:00Z", "new", None,
            ),
        ],
    )
    db.commit()
    calls = []

    async def analyze(**kwargs):
        calls.append(kwargs["user"])
        return {
            "sentiment": "neutral", "symbols": [], "relevance": 0.7, "impact": "ok",
        }, None

    monkeypatch.setattr(news_analyzer, "llm_structured", analyze)

    assert await news_analyzer.analyze_pending_news(batch_size=3) == 1
    assert len(calls) == 1 and "Ethereum new" in calls[0]
    assert db.execute(
        "SELECT status FROM news_items WHERE id='retry-not-due'"
    ).fetchone()["status"] == "analysis_retry"


@pytest.mark.asyncio
async def test_analyzer_fairly_interleaves_old_new_and_due_retry_rows(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.executemany(
        "INSERT INTO news_items "
        "(id, title, source, summary, published_at, status, analyzed_at) "
        "VALUES (?, ?, 'fixture', '', ?, ?, ?)",
        [
            ("retry-old", "Bitcoin retry old", "2026-07-19T00:00:00Z", "analysis_retry", "2020-01-01T00:00:00Z"),
            ("new-old", "Ethereum new old", "2026-07-19T00:01:00Z", "new", None),
            ("retry-new", "Solana retry new", "2026-07-19T00:02:00Z", "analysis_retry", "2020-01-02T00:00:00Z"),
            ("new-new", "Cardano new new", "2026-07-19T00:03:00Z", "new", None),
        ],
    )
    db.commit()
    order = []

    async def analyze(**kwargs):
        order.append(kwargs["user"].splitlines()[0].removeprefix("Headline: "))
        return {
            "sentiment": "neutral", "symbols": [], "relevance": 0.7, "impact": "ok",
        }, None

    monkeypatch.setattr(news_analyzer, "llm_structured", analyze)

    assert await news_analyzer.analyze_pending_news(batch_size=3) == 3
    assert order == ["Bitcoin retry old", "Ethereum new old", "Solana retry new"]


@pytest.mark.asyncio
async def test_analyzer_persists_exact_entities_not_model_symbol_guesses(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items (id, title, source, summary, published_at, status) "
        "VALUES ('news-ok', 'Solana demand rises', 'fixture', '', '2026-07-19T00:00:00Z', 'new')"
    )
    db.commit()

    async def analyze(**_kwargs):
        return {
            "sentiment": "bullish",
            "symbols": ["DOGE/USDT"],
            "relevance": 0.8,
            "impact": "Demand may support price.",
        }, None

    monkeypatch.setattr(news_analyzer, "llm_structured", analyze)

    assert await news_analyzer.analyze_pending_news(batch_size=1) == 1
    row = db.execute(
        "SELECT status, symbols_mentioned, relevance_score FROM news_items WHERE id='news-ok'"
    ).fetchone()
    assert row["status"] == "analyzed"
    assert json.loads(row["symbols_mentioned"]) == ["SOL/USDT"]
    assert row["relevance_score"] == pytest.approx(0.8)


def test_news_analysis_is_an_independent_bounded_scheduler_job(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.data import news_analyzer

    calls = []

    async def analyze_pending_news(*, batch_size):
        calls.append(batch_size)
        return 1

    def run_bounded(job_id, awaitable):
        calls.append(job_id)
        return asyncio.run(awaitable)

    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            news=SimpleNamespace(enabled=True), runtime=SimpleNamespace(paused=False)
        ),
    )
    monkeypatch.setattr(news_analyzer, "analyze_pending_news", analyze_pending_news)
    monkeypatch.setattr(scheduler, "_run_async_with_timeout", run_bounded)

    scheduler._run_news_analyze()

    assert scheduler._ASYNC_JOB_TIMEOUT_SEC["news_analyze"] <= 120
    assert calls == ["news_analyze", 3]


def test_news_analysis_does_not_start_while_runtime_is_paused(monkeypatch):
    from aletheia.agent_runtime import scheduler

    calls = []
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(
            news=SimpleNamespace(enabled=True), runtime=SimpleNamespace(paused=True)
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "_run_async_with_timeout",
        lambda _job_id, awaitable: (awaitable.close(), calls.append("started")),
    )

    scheduler._run_news_analyze()

    assert calls == []


def test_shared_async_timeout_worker_allows_news_and_rss_coroutines_to_progress():
    from aletheia.agent_runtime import scheduler

    worker = scheduler._PersistentAsyncTimeoutWorker()
    release = asyncio.Event()

    async def news_analysis():
        await asyncio.wait_for(release.wait(), timeout=1)
        return "analysis"

    async def rss_fetch():
        release.set()
        await asyncio.sleep(0)
        return "rss"

    analysis_future = worker.submit(news_analysis())
    rss_future = worker.submit(rss_fetch())
    try:
        assert rss_future.result(timeout=1) == "rss"
        assert analysis_future.result(timeout=1) == "analysis"
    finally:
        worker.close()


def test_sync_blocked_analyzer_does_not_block_rss_timeout_lane(monkeypatch):
    from aletheia.agent_runtime import scheduler

    started = threading.Event()
    release = threading.Event()
    analyzer_errors = []

    async def sync_blocked_analyzer():
        started.set()
        release.wait(timeout=1)
        return "analysis"

    async def rss_fetch():
        await asyncio.sleep(0)
        return "rss"

    monkeypatch.setattr(scheduler, "_ensure_agent_run_ended", lambda *_args: None)
    monkeypatch.setitem(scheduler._ASYNC_JOB_TIMEOUT_SEC, "news_analyze", 2.0)
    monkeypatch.setitem(scheduler._ASYNC_JOB_TIMEOUT_SEC, "news_fetch", 0.2)

    def run_analyzer():
        try:
            scheduler._run_async_with_timeout("news_analyze", sync_blocked_analyzer())
        except Exception as exc:  # pragma: no cover - assertion below reports it
            analyzer_errors.append(exc)

    thread = threading.Thread(target=run_analyzer)
    thread.start()
    assert started.wait(timeout=1)
    started_at = time.monotonic()
    try:
        assert scheduler._run_async_with_timeout("news_fetch", rss_fetch()) == "rss"
        assert time.monotonic() - started_at < 0.2
    finally:
        release.set()
        thread.join(timeout=2)
        scheduler._async_timeout_worker.close()
        analyzer_worker = getattr(scheduler, "_news_analyzer_timeout_worker", None)
        if analyzer_worker is not None:
            analyzer_worker.close()
    assert analyzer_errors == []


def test_stop_scheduler_closes_default_and_analyzer_timeout_workers(monkeypatch):
    from aletheia.agent_runtime import scheduler

    closed = []

    class Worker:
        def close(self):
            closed.append(self)

    class Scheduler:
        def shutdown(self, *, wait):
            assert wait is False

    analyzer_worker = scheduler._news_analyzer_timeout_worker
    default_worker = Worker()
    dedicated_worker = Worker()
    monkeypatch.setattr(scheduler, "_scheduler", Scheduler())
    monkeypatch.setattr(scheduler, "_async_timeout_worker", default_worker)
    monkeypatch.setattr(scheduler, "_news_analyzer_timeout_worker", dedicated_worker)

    scheduler.stop_scheduler()

    assert analyzer_worker is not None
    assert closed == [default_worker, dedicated_worker]


def test_join_timed_out_async_worker_fails_closed_without_replacement(monkeypatch):
    from aletheia.agent_runtime import scheduler

    worker = scheduler._PersistentAsyncTimeoutWorker("closed-analyzer-worker")
    stopped = []
    joined = []

    class BlockedLoop:
        def stop(self):
            stopped.append("stop")

        def call_soon_threadsafe(self, callback):
            callback()

    class BlockedThread:
        def is_alive(self):
            return True

        def join(self, *, timeout):
            joined.append(timeout)

    worker._loop = BlockedLoop()
    worker._thread = BlockedThread()

    async def complete():
        return "ok"

    worker.close()
    assert stopped == ["stop"]
    assert joined == [5]
    assert worker.closed is True
    rejected = complete()
    with pytest.raises(RuntimeError, match="closed"):
        worker.submit(rejected)
    assert rejected.cr_frame is None

    monkeypatch.setattr(scheduler, "_news_analyzer_timeout_worker", worker)
    with pytest.raises(RuntimeError, match="shutdown incomplete"):
        scheduler._ensure_timeout_workers_ready()
    assert scheduler._news_analyzer_timeout_worker is worker


def test_research_news_revalidates_historical_entity_tags(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agents.research_worker import _get_recent_news_for_research
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.executemany(
        "INSERT INTO news_items "
        "(id, title, source, summary, content, impact_summary, sentiment, "
        " symbols_mentioned, relevance_score, fetched_at, published_at, status) "
        "VALUES (?, ?, 'fixture', '', '', 'impact', 'neutral', ?, 0.9, ?, ?, 'analyzed')",
        [
            (
                "historical-false-sol",
                "New solution improves adoption",
                '["SOL/USDT"]',
                now,
                now,
            ),
            (
                "historical-exact-sol",
                "Solana validator demand rises",
                '["WRONG/USDT"]',
                now,
                now,
            ),
        ],
    )
    db.commit()

    rendered = _get_recent_news_for_research()

    assert "New solution improves adoption" not in rendered
    assert "Solana validator demand rises" in rendered
    assert "[SOL/USDT]" in rendered
    assert "WRONG/USDT" not in rendered


@pytest.mark.asyncio
async def test_manual_analyze_one_item_success_persists_all_structured_fields(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items (id, title, source, summary, published_at, status) "
        "VALUES ('manual-ok', 'Solana demand rises', 'fixture', 'sol summary', "
        "'2026-07-19T00:00:00Z', 'new')"
    )
    db.commit()

    async def good_analysis(**_kwargs):
        return {
            "sentiment": "bullish",
            "symbols": ["DOGE/USDT"],
            "relevance": 0.85,
            "impact": "Price support expected.",
        }, None

    monkeypatch.setattr(news_analyzer, "llm_structured", good_analysis)

    result = await news_analyzer.analyze_one_item("manual-ok")

    assert result["success"] is True
    assert result["status"] == "analyzed"
    item = result["item"]
    assert item["sentiment"] == "bullish"
    assert item["relevance_score"] == pytest.approx(0.85)
    assert item["impact_summary"] == "Price support expected."
    assert item["analyzed_at"] is not None

    row = db.execute(
        "SELECT status, sentiment, relevance_score, impact_summary, symbols_mentioned, analyzed_at "
        "FROM news_items WHERE id='manual-ok'"
    ).fetchone()
    assert row["status"] == "analyzed"
    assert row["sentiment"] == "bullish"
    assert row["relevance_score"] == pytest.approx(0.85)
    assert row["impact_summary"] == "Price support expected."
    assert row["analyzed_at"] is not None
    # entity matcher is used, not the model's symbol guess
    assert json.loads(row["symbols_mentioned"]) == ["SOL/USDT"]
    # response matches DB
    assert item["sentiment"] == row["sentiment"]
    assert item["impact_summary"] == row["impact_summary"]


@pytest.mark.asyncio
async def test_manual_analyze_one_item_failure_never_marks_analyzed(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items (id, title, source, summary, published_at, status) "
        "VALUES ('manual-fail', 'Bitcoin update', 'fixture', '', "
        "'2026-07-19T00:00:00Z', 'new')"
    )
    db.commit()

    async def provider_error(**_kwargs):
        return None, "provider unavailable"

    monkeypatch.setattr(news_analyzer, "llm_structured", provider_error)

    result = await news_analyzer.analyze_one_item("manual-fail")

    assert result["success"] is False
    assert result["status"] == "analysis_retry"
    assert result["retry_eligible"] is True

    row = db.execute(
        "SELECT status FROM news_items WHERE id='manual-fail'"
    ).fetchone()
    assert row["status"] == "analysis_retry"


@pytest.mark.asyncio
async def test_manual_analyze_already_analyzed_returns_persisted_without_rerun(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.data import news_analyzer
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "INSERT INTO news_items "
        "(id, title, source, summary, published_at, status, sentiment, "
        " relevance_score, impact_summary, symbols_mentioned, analyzed_at) "
        "VALUES ('manual-done', 'ETH upgrade', 'fixture', '', "
        "'2026-07-19T00:00:00Z', 'analyzed', 'bullish', 0.9, 'Upgrade expected.', "
        "'[\"ETH/USDT\"]', '2026-07-19T10:00:00Z')"
    )
    db.commit()

    llm_calls = []

    async def should_not_call(**_kwargs):
        llm_calls.append(True)
        return {"sentiment": "bearish", "symbols": [], "relevance": 0.1, "impact": "x"}, None

    monkeypatch.setattr(news_analyzer, "llm_structured", should_not_call)

    result = await news_analyzer.analyze_one_item("manual-done")

    assert llm_calls == [], "LLM must not be called for already-analyzed items"
    assert result["success"] is True
    assert result["status"] == "analyzed"
    item = result["item"]
    assert item["sentiment"] == "bullish"
    assert item["impact_summary"] == "Upgrade expected."
    assert item["symbols_mentioned"] == ["ETH/USDT"]
