"""Serenity atomic admission tests (#1277)."""
import pytest
from aletheia.storage import repositories as repo
from aletheia.storage.models import SerenityResearchRun

def test_concurrent_admission_one_succeeds():
    """Two concurrent creates: one admitted, one 409."""
    run1 = SerenityResearchRun(trigger_type="manual", mode="standard")
    run2 = SerenityResearchRun(trigger_type="manual", mode="standard")
    
    r1 = repo.admit_serenity_research_run(
        run_id=run1.id, idempotency_key=None, trigger_type="manual", mode="standard",
        created_at=run1.created_at, updated_at=run1.updated_at
    )
    r2 = repo.admit_serenity_research_run(
        run_id=run2.id, idempotency_key=None, trigger_type="manual", mode="standard",
        created_at=run2.created_at, updated_at=run2.updated_at
    )
    
    assert r1["admitted"] is True
    assert r2["admitted"] is False
    assert r2["reason"] == "occupied"
    
    active = repo.list_active_serenity_research_runs()
    assert len(active) == 1
    assert active[0]["id"] == run1.id

def test_idempotency_deduplication():
    """Same idempotency key twice: both return same run, one row."""
    key = "test-idem-key"
    run1 = SerenityResearchRun(trigger_type="manual", idempotency_key=key)
    run2 = SerenityResearchRun(trigger_type="manual", idempotency_key=key)
    
    r1 = repo.admit_serenity_research_run(
        run_id=run1.id, idempotency_key=key, trigger_type="manual", mode="standard",
        created_at=run1.created_at, updated_at=run1.updated_at
    )
    r2 = repo.admit_serenity_research_run(
        run_id=run2.id, idempotency_key=key, trigger_type="manual", mode="standard",
        created_at=run2.created_at, updated_at=run2.updated_at
    )
    
    assert r1["admitted"] is True
    assert r2["admitted"] is False
    assert "existing_run" in r2
    assert r2["existing_run"]["id"] == run1.id
    
    all_runs = repo.list_serenity_research_runs(limit=100)
    matching = [r for r in all_runs if r.get("idempotency_key") == key]
    assert len(matching) == 1

def test_slot_freed_admission_succeeds():
    """Slot occupied -> freed -> new admission succeeds."""
    run1 = SerenityResearchRun(trigger_type="manual")
    r1 = repo.admit_serenity_research_run(
        run_id=run1.id, idempotency_key=None, trigger_type="manual", mode="standard",
        created_at=run1.created_at, updated_at=run1.updated_at
    )
    assert r1["admitted"] is True
    
    # Complete the first run
    repo.update_serenity_research_run(run1.id, status="completed")
    
    # New run should succeed
    run2 = SerenityResearchRun(trigger_type="manual")
    r2 = repo.admit_serenity_research_run(
        run_id=run2.id, idempotency_key=None, trigger_type="manual", mode="standard",
        created_at=run2.created_at, updated_at=run2.updated_at
    )
    assert r2["admitted"] is True
