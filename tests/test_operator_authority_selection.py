"""Retired Python Operator cannot regain authority through config or API."""

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aletheia.config import loader
from aletheia.config.schema import AletheiaConfig


@pytest.mark.parametrize("operator", [None, {"enabled": False, "loop_enabled": True}, {"enabled": True, "loop_enabled": True}])
def test_historical_operator_config_discards_loop_without_enabling_shell(monkeypatch, tmp_path, operator):
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(loader, "is_preview_mode", lambda: True)
    monkeypatch.setattr(loader, "_dirs_ensured_for", None)
    monkeypatch.setattr(loader, "_config_cache", None)
    monkeypatch.setattr(loader, "_emit_consistency_events", lambda _cfg: None)
    raw = AletheiaConfig().model_dump(mode="json")
    raw.pop("operator")
    if operator is not None:
        raw["operator"] = operator
    original = yaml.safe_dump(raw)
    loader.CONFIG_PATH.write_text(original, encoding="utf-8")

    cfg = loader.load_config(persist_migrations=False)
    assert cfg.operator.enabled is (operator is not None and operator["enabled"])
    assert "loop_enabled" not in cfg.operator.model_dump()
    assert loader.CONFIG_PATH.read_text(encoding="utf-8") == original

    cfg = loader.load_config()
    persisted = yaml.safe_load(loader.CONFIG_PATH.read_text(encoding="utf-8"))
    assert persisted["operator"]["enabled"] is cfg.operator.enabled
    assert "loop_enabled" not in persisted["operator"]
    assert loader.load_config().operator.enabled is cfg.operator.enabled


@pytest.mark.anyio
async def test_retired_operator_mutations_are_absent_and_history_is_readable():
    from aletheia.server.routes import operator, settings
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import KnowledgeItem

    item = KnowledgeItem(
        item_type="operator_assessment", title="Historical assessment",
        content="Historical assessment body", tags='{"actions_decided": 1}',
    )
    repo.insert_knowledge_item(item)
    before = repo.get_knowledge_item(item.id)
    app = FastAPI()
    app.include_router(operator.router)
    app.include_router(settings.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/operator/loop/trigger")).status_code == 404
        assert (await client.post("/api/settings/operator", json={"loop_enabled": True})).status_code == 404
        response = await client.get("/api/operator/loop/latest")
    assert response.status_code == 200
    assert response.json() == [{
        "id": item.id, "title": item.title, "created_at": before["created_at"],
        "summary": {"actions_decided": 1},
    }]
    assert repo.get_knowledge_item(item.id) == before
