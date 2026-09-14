"""Behavioral contract for Opportunity Information Integrity P1-03..P1-05."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "opportunity_information_integrity"


@spec_claim(
    SpecClaim(
        "OII-POLICY-01",
        _CONTRACT,
        "policy epoch attribution",
        "L2b",
        "The combined behavior repair has one durable, idempotent policy epoch and a "
        "stable owner-readable label.",
        status="implemented",
    )
)
def test_information_integrity_policy_epoch_is_durable_and_idempotent():
    from aletheia.analytics.filter_evidence import POLICY_EPOCH_LABEL
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
        opportunity_information_policy_context,
    )

    first = ensure_opportunity_information_policy_epoch()
    second = ensure_opportunity_information_policy_epoch()

    assert POLICY_EPOCH_LABEL == "opportunity_information_integrity/v4"
    assert opportunity_information_policy_context()["supported_analysis_contexts"] == [
        "crypto", "stock_linked_perp",
    ]
    assert first == second
    assert opportunity_information_policy_context()["operator_evidence_manifest"] == (
        "operator-candidate-v3"
    )
    assert opportunity_information_policy_context()["structured_trade_motivation"] == (
        "structured-trade-motivation/v1"
    )
    assert opportunity_information_policy_context()["universe_opportunity_producer"] == (
        "universe_scanner_only"
    )
    assert opportunity_information_policy_context()["market_observer"] == (
        "advisory_enrichment_only"
    )
    assert opportunity_information_policy_context()["ranker_requires_signal_emitter_fact"] is True


def test_structured_trade_motivation_rotates_information_policy_epoch():
    from aletheia.learning.opportunity_information_policy import (
        _AFFECTED_PATHS,
        opportunity_information_policy_context,
    )
    from aletheia.learning.policy_epochs import build_policy_epoch

    current_values = opportunity_information_policy_context()
    legacy_values = dict(current_values)
    legacy_values.pop("structured_trade_motivation")
    current = build_policy_epoch(
        "opportunity_information_integrity",
        current_values,
        affected_path_ids=_AFFECTED_PATHS,
    )
    legacy = build_policy_epoch(
        "opportunity_information_integrity",
        legacy_values,
        affected_path_ids=tuple(
            path for path in _AFFECTED_PATHS if path != "structured_trade_motivation"
        ),
    )

    assert current.epoch_hash != legacy.epoch_hash


@pytest.mark.real_db
def test_information_policy_epoch_has_one_open_row_under_concurrency(
    tmp_path, monkeypatch,
):
    import aletheia.config.loader as loader
    from aletheia.learning.opportunity_information_policy import (
        ensure_opportunity_information_policy_epoch,
    )
    from aletheia.storage import sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()
    barrier = threading.Barrier(8)

    def ensure():
        barrier.wait(timeout=5)
        return ensure_opportunity_information_policy_epoch()

    with ThreadPoolExecutor(max_workers=8) as pool:
        epoch_ids = list(pool.map(lambda _index: ensure(), range(8)))

    rows = sqlite.get_db().execute(
        "SELECT epoch_id FROM learning_policy_epochs "
        "WHERE epoch_kind='opportunity_information_integrity' AND ended_at IS NULL"
    ).fetchall()
    assert len(rows) == 1
    assert set(epoch_ids) == {rows[0]["epoch_id"]}


@pytest.mark.real_db
def test_policy_epoch_concurrent_rotation_leaves_only_one_open_row(
    tmp_path, monkeypatch,
):
    import aletheia.config.loader as loader
    from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch
    from aletheia.storage import sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()
    barrier = threading.Barrier(8)

    def rotate(index: int):
        epoch = build_policy_epoch(
            "concurrent_rotation_test",
            {"version": index % 2},
            affected_path_ids=("test_path",),
        )
        barrier.wait(timeout=5)
        return persist_policy_epoch(epoch)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(rotate, range(8)))

    rows = sqlite.get_db().execute(
        "SELECT epoch_id, ended_at FROM learning_policy_epochs "
        "WHERE epoch_kind='concurrent_rotation_test'"
    ).fetchall()
    assert sum(row["ended_at"] is None for row in rows) == 1
    assert sum(row["ended_at"] is not None for row in rows) == len(rows) - 1


@pytest.mark.real_db
def test_policy_epoch_rotation_uses_one_monotonic_boundary(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.learning.policy_epochs as policy_epochs
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningPolicyEpoch
    from aletheia.storage import sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()

    from datetime import datetime, timezone

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 22, 11, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(policy_epochs, "datetime", FixedDateTime)

    def add_epoch(epoch_id, epoch_kind, effective_values_hash, started_at):
        insert_record(
            "learning_policy_epochs",
            LearningPolicyEpoch(
                epoch_id=epoch_id,
                epoch_kind=epoch_kind,
                effective_values_hash=effective_values_hash,
                effective_values_json="{}",
                affected_path_ids_json="[]",
                started_at=started_at,
            ),
        )

    future = "2026-08-22T12:00:00+00:00"
    add_epoch("future-open", "clock-rollback", "old-hash", future)

    epoch = policy_epochs.build_policy_epoch(
        "clock-rollback", {"version": "new"}, affected_path_ids=("test",)
    )
    policy_epochs.persist_policy_epoch(epoch)
    rows = sqlite.get_db().execute(
        "SELECT started_at, ended_at FROM learning_policy_epochs "
        "WHERE epoch_kind='clock-rollback' ORDER BY rowid"
    ).fetchall()
    assert rows[0]["ended_at"] == rows[1]["started_at"]
    assert rows[0]["ended_at"] == "2026-08-22T12:00:00.000001+00:00"

    add_epoch("matching-open", "matching-cleanup", "matching-hash", future)
    add_epoch(
        "older-open", "matching-cleanup", "older-hash", "2026-08-22T11:59:00+00:00"
    )
    matching = policy_epochs.PolicyEpoch(
        epoch_kind="matching-cleanup",
        epoch_hash="matching-hash",
        effective_values={},
        affected_path_ids=(),
        safety_required=False,
    )
    policy_epochs.persist_policy_epoch(matching)
    rows = sqlite.get_db().execute(
        "SELECT epoch_id, started_at, ended_at FROM learning_policy_epochs "
        "WHERE epoch_kind='matching-cleanup' ORDER BY rowid"
    ).fetchall()
    assert rows[0]["ended_at"] is None
    assert rows[1]["ended_at"] == "2026-08-22T12:00:00.000001+00:00"
    add_epoch("malformed-start", "malformed-start", "old-hash", "not-a-timestamp")
    malformed = policy_epochs.PolicyEpoch(
        epoch_kind="malformed-start",
        epoch_hash="new-hash",
        effective_values={},
        affected_path_ids=(),
        safety_required=False,
    )
    with pytest.raises(ValueError, match="Invalid started_at"):
        policy_epochs.persist_policy_epoch(malformed)
    row = sqlite.get_db().execute(
        "SELECT ended_at FROM learning_policy_epochs WHERE epoch_id='malformed-start'"
    ).fetchone()
    assert row["ended_at"] is None


def test_market_observer_entry_persists_information_epoch_before_early_return():
    from aletheia.agents.market_observer import run_market_observer
    from aletheia.storage.sqlite import get_db

    result = asyncio.run(run_market_observer(permission_mode="invalid"))

    assert result["outputs"] == []
    row = get_db().execute(
        "SELECT epoch_id FROM learning_policy_epochs "
        "WHERE epoch_kind='opportunity_information_integrity' AND ended_at IS NULL"
    ).fetchone()
    assert row is not None




def _serenity_opportunity(theme_id: str, opportunity_id: str, *, status: str = "active"):
    from aletheia.storage.models import SerenityOpportunity

    return SerenityOpportunity(
        id=opportunity_id,
        theme_id=theme_id,
        symbol=f"{opportunity_id.upper()}/USDT",
        direction="long",
        market_type="spot",
        thesis=f"exact thesis for {opportunity_id}",
        confidence_score=80,
        assessed_tier="speculative",
        researchability_grade="C",
        status=status,
    )






@spec_claim(
    SpecClaim(
        "OII-P1-04B",
        _CONTRACT,
        "P1-04 evidence reassessment",
        "L2b",
        "Each evidence insert atomically recomputes assessed tier and researchability for every "
        "active sibling without changing terminal opportunities.",
        status="implemented",
    )
)
def test_evidence_insert_reassesses_only_active_siblings():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityEvidence, SerenityTheme

    theme = SerenityTheme(id="reassess-theme", name="Reassess")
    repo.insert_serenity_theme(theme)
    active_a = _serenity_opportunity(theme.id, "active-a")
    active_b = _serenity_opportunity(theme.id, "active-b")
    reviewed = _serenity_opportunity(theme.id, "reviewed", status="reviewed")
    reviewed.assessed_tier = "likely"
    reviewed.researchability_grade = "B"
    for opportunity in (active_a, active_b, reviewed):
        repo.insert_serenity_opportunity(opportunity)

    for index in range(2):
        repo.insert_serenity_evidence(
            SerenityEvidence(
                id=f"reassess-evidence-{index}",
                theme_id=theme.id,
                title=f"Official source {index}",
                source="official",
                source_ref=f"https://example.com/source-{index}",
                url=f"https://example.com/source-{index}",
                tier="high_confidence",
                assessed_tier="high_confidence",
                assessed_by="deterministic_v1",
            )
        )

    for opportunity_id in (active_a.id, active_b.id):
        row = repo.get_serenity_opportunity(opportunity_id)
        assert row["assessed_tier"] == "high_confidence"
        assert row["researchability_grade"] == "A"
    terminal = repo.get_serenity_opportunity(reviewed.id)
    assert terminal["status"] == "reviewed"
    assert terminal["assessed_tier"] == "likely"
    assert terminal["researchability_grade"] == "B"


def test_evidence_insert_returns_active_siblings_after_commit():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityEvidence, SerenityTheme
    from aletheia.storage.sqlite import get_db

    theme = SerenityTheme(id="evidence-retry-theme", name="Evidence retry")
    repo.insert_serenity_theme(theme)
    opportunity = _serenity_opportunity(theme.id, "evidence-retry")
    opportunity.confidence_score = 90
    opportunity.researchability_grade = "A"
    repo.insert_serenity_opportunity(opportunity)

    retry_ids = repo.insert_serenity_evidence(SerenityEvidence(
        id="evidence-crosses-threshold",
        theme_id=theme.id,
        title="Official disclosure",
        source="official",
        source_ref="https://sec.gov/evidence-retry",
        url="https://sec.gov/evidence-retry",
        tier="disclosed",
        assessed_tier=None,
    ))

    assert retry_ids == [opportunity.id]
    assert get_db().in_transaction is False










@spec_claim(
    SpecClaim(
        "OII-P1-05B",
        _CONTRACT,
        "P1-05 historical exact skip repair",
        "L2b",
        "The idempotent migration repairs only active Serenity sources with an exact final skip.",
        status="implemented",
    )
)
def test_migration_repairs_only_historical_exact_serenity_final_skips():
    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import PiDecision, SerenityTheme

    theme = SerenityTheme(id="history-theme", name="History", status="promoted")
    repo.insert_serenity_theme(theme)
    exact = _serenity_opportunity(theme.id, "history-exact")
    mismatch = _serenity_opportunity(theme.id, "history-mismatch")
    repo.insert_serenity_opportunity(exact)
    repo.insert_serenity_opportunity(mismatch)
    for opportunity, symbol in ((exact, exact.symbol), (mismatch, "WRONG/USDT")):
        repo.insert_pi_decision(
            PiDecision(
                id=f"final-{opportunity.id}",
                action="skip",
                opportunity_id=opportunity.id,
                signal_source="serenity",
                symbol=symbol,
                direction=opportunity.direction,
                market_type=opportunity.market_type,
            )
        )

    run_migrations()
    run_migrations()

    assert repo.get_serenity_opportunity(exact.id)["status"] == "reviewed"
    assert repo.get_serenity_opportunity(mismatch.id)["status"] == "active"


def test_migration_leaves_ambiguous_serenity_skip_and_trade_active():
    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import PiDecision, SerenityTheme

    theme = SerenityTheme(id="history-ambiguous-theme", name="History ambiguous")
    repo.insert_serenity_theme(theme)
    opportunity = _serenity_opportunity(theme.id, "history-ambiguous")
    repo.insert_serenity_opportunity(opportunity)
    for action in ("skip", "trade"):
        repo.insert_pi_decision(PiDecision(
            id=f"ambiguous-{action}",
            action=action,
            opportunity_id=opportunity.id,
            signal_source="serenity",
            symbol=opportunity.symbol,
            direction=opportunity.direction,
            market_type=opportunity.market_type,
        ))

    run_migrations()

    assert repo.get_serenity_opportunity(opportunity.id)["status"] == "active"


@spec_claim(
    SpecClaim(
        "OII-P1-06",
        _CONTRACT,
        "P1-06 exact complete post-trade context",
        "L2b",
        "Post-trade attribution reads the unique PiDecision by proposal_id and preserves complete "
        "reasoning, bear case, assumptions, Proposal rationale, and both manifest generations.",
        status="implemented",
    )
)
def test_post_trade_context_uses_exact_proposal_id_without_truncation():
    from aletheia.analytics.trade_attribution import proposal_decision_context
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision, TradeProposal

    proposal_id = "proposal-context-exact"
    rationale = "complete proposal rationale " + ("p" * 700)
    reasoning = "complete PiDecision reasoning " + ("r" * 900)
    repo.insert_trade_proposal(
        TradeProposal(
            id=proposal_id,
            opportunity_id="source-context-exact",
            execution_mode="bitget_demo",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            price=100.0,
            agent_rationale=rationale,
        )
    )
    repo.insert_pi_decision(
        PiDecision(
            id="decision-context-decoy",
            proposal_id="another-proposal",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            action="trade",
            reasoning="same symbol decoy must not be selected",
        )
    )
    repo.insert_pi_decision(
        PiDecision(
            id="decision-context-exact",
            proposal_id=proposal_id,
            opportunity_id="source-context-exact",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            action="trade",
            reasoning=reasoning,
            bear_case="complete bear case",
            key_assumptions='["assumption one"]',
            evidence_manifest_json='{"version":"evidence-v1","ids":["e1"]}',
            learning_manifest_json='{"version":"learning-v1","ids":["l1"]}',
        )
    )

    context = proposal_decision_context(proposal_id)

    assert context["pi_decision_reasoning"] == reasoning
    assert context["pi_decision_bear_case"] == "complete bear case"
    assert context["pi_decision_assumptions"] == ["assumption one"]
    assert context["proposal_agent_rationale"] == rationale
    assert context["decision_evidence_manifest"]["ids"] == ["e1"]
    assert context["decision_learning_manifest"]["ids"] == ["l1"]


def test_same_proposal_duplicate_finals_are_explicit_integrity_error():
    from aletheia.analytics.trade_attribution import proposal_decision_context
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision, TradeProposal

    proposal_id = "proposal-context-ambiguous"
    repo.insert_trade_proposal(TradeProposal(
        id=proposal_id,
        opportunity_id="source-context-ambiguous",
        execution_mode="bitget_demo",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        agent_rationale="exact proposal rationale remains available",
    ))
    for index, action in enumerate(("trade", "skip")):
        repo.insert_pi_decision(PiDecision(
            id=f"decision-context-ambiguous-{index}",
            proposal_id=proposal_id,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            action=action,
            reasoning=f"ambiguous {action}",
        ))

    context = proposal_decision_context(proposal_id)

    assert context["decision_context_status"] == "ambiguous_integrity_error"
    assert context["pi_decision_reasoning"] == "ambiguous_integrity_error"
    assert context["decision_evidence_manifest"] == "ambiguous_integrity_error"
    assert context["proposal_agent_rationale"] == "exact proposal rationale remains available"
