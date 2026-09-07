"""Retained lifecycle contracts: research expiry and canonical attention reads."""

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "DL-S64", "decision_lifecycle_contract", "§2.3 serenity maintenance expiry", "L2b",
        "expire_stale_serenity_themes() transitions themes past their expires_at "
        "deadline to 'expired' and cascades to active child opportunities so the "
        "pending-signal queue treats them as stale (#199).",
        status="implemented",
    ),
)
def test_expire_stale_serenity_themes_cascades_to_children(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import SerenityOpportunity, SerenityTheme
    from aletheia.storage.repositories import (
        expire_stale_serenity_themes,
        get_serenity_opportunity,
        get_serenity_theme,
        insert_serenity_opportunity,
        insert_serenity_theme,
        update_serenity_theme,
    )

    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    theme = SerenityTheme(name="Deadline-elapsed thesis", status="active")
    insert_serenity_theme(theme)
    update_serenity_theme(theme.id, expires_at=past)
    opp = SerenityOpportunity(theme_id=theme.id, symbol="ETH", direction="long", status="active")
    insert_serenity_opportunity(opp)

    count = expire_stale_serenity_themes()

    assert count >= 1
    assert get_serenity_theme(theme.id)["status"] == "expired"
    assert get_serenity_opportunity(opp.id)["status"] == "expired"


@spec_claim(
    SpecClaim(
        "DL-S75",
        "decision_lifecycle_contract",
        "§2.1a Candidate attention read projection",
        "L2b",
        "Candidate detail and Planning History project elapsed soft/hard attention "
        "deadlines as ttl_expired/max_attention_age without mutating durable state.",
        status="implemented",
    ),
)
def test_candidate_attention_read_projection_is_effective_and_non_mutating():
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.repositories import _effective_candidate_attention

    start = datetime(2026, 8, 28, tzinfo=timezone.utc)
    row = {
        "attention_status": "active",
        "attention_terminal_at": None,
        "attention_terminal_reason": None,
        "soft_expires_at": (start + timedelta(minutes=15)).isoformat(),
        "hard_expires_at": (start + timedelta(hours=1)).isoformat(),
    }

    soft = _effective_candidate_attention(row, now=start + timedelta(minutes=16))
    hard = _effective_candidate_attention(row, now=start + timedelta(hours=2))

    assert soft["attention_terminal_reason"] == "ttl_expired"
    assert hard["attention_terminal_reason"] == "max_attention_age"
    assert row["attention_status"] == "active"
    assert row["attention_terminal_reason"] is None
