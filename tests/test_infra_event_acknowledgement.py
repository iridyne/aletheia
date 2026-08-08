from types import SimpleNamespace
import threading

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = pytest.mark.real_db


@pytest.fixture
async def client(api_only_runtime):
    from aletheia.server.app import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_operator_can_acknowledge_current_infra_events(client):
    from aletheia.infra.events import clear_infra_events, emit_infra_event

    clear_infra_events()
    emit_infra_event(
        severity="warning",
        source="test",
        code="test_warning",
        message="current warning",
        dedupe_sec=300,
    )

    response = await client.post("/api/infra/events/acknowledge")

    assert response.status_code == 200
    assert response.json() == {"acknowledged": 1}
    assert (await client.get("/api/infra/events")).json() == {"events": []}
    assert emit_infra_event(
        severity="warning",
        source="test",
        code="test_warning",
        message="same warning",
        dedupe_sec=300,
    ) is None


@pytest.mark.asyncio
async def test_news_fetch_does_not_reuse_connection_closed_during_network_await(
    api_only_runtime, monkeypatch,
):
    from aletheia.data import news_collector
    from aletheia.learning import opportunity_information_policy
    from aletheia.storage.sqlite import close_db, get_db

    feed_started = threading.Event()
    release_feed = threading.Event()

    def delayed_feed(_url):
        feed_started.set()
        assert release_feed.wait(timeout=2)
        return [{"id": "one", "title": "Bitcoin update", "link": "https://example.test/one"}]

    feed = SimpleNamespace(enabled=True, url="https://example.test/rss", name="test", source="test")
    cfg = SimpleNamespace(news=SimpleNamespace(enabled=True, feeds=[feed]))
    monkeypatch.setattr(news_collector, "load_config", lambda: cfg)
    monkeypatch.setattr(news_collector, "_fetch_feed_sync", delayed_feed)
    monkeypatch.setattr(
        opportunity_information_policy,
        "ensure_opportunity_information_policy_epoch",
        lambda: None,
    )

    async def close_shared_thread_connection():
        import asyncio

        while not feed_started.is_set():
            await asyncio.sleep(0)
        # Reproduce another async scheduler job's cleanup on the shared loop.
        get_db()
        close_db()
        release_feed.set()

    import asyncio

    result, _ = await asyncio.gather(
        news_collector.fetch_news_feeds(), close_shared_thread_connection()
    )

    assert result == {"fetched": 1, "new": 1}
