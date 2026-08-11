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
    from aletheia.learning.opportunity_information_policy import (
        POLICY_EPOCH_LABEL,
        ensure_opportunity_information_policy_epoch,
        opportunity_information_policy_context,
    )

    first = ensure_opportunity_information_policy_epoch()
    second = ensure_opportunity_information_policy_epoch()

    assert POLICY_EPOCH_LABEL == "opportunity_information_integrity/v2"
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


def test_information_epoch_is_persisted_before_zero_pending_return(monkeypatch):
    from aletheia.agent_shell import commands, trading_context
    from aletheia.agent_shell import b2_evidence_policy, skip_policy, vergex_policy
    from aletheia.learning import opportunity_information_policy

    events = []
    monkeypatch.setattr(
        opportunity_information_policy,
        "ensure_opportunity_information_policy_epoch",
        lambda: events.append("information_epoch") or "information-epoch-id",
    )
    monkeypatch.setattr(
        trading_context,
        "build_enriched_trading_context",
        lambda **kwargs: {
            "layer1": {"data_health": {}},
            "layer2": {"pending_queue": {"unresolved_total": 0}},
            "layer3": {},
        },
    )
    monkeypatch.setattr(
        skip_policy, "ensure_skip_remediation_policy_epoch", lambda: "skip-epoch"
    )
    monkeypatch.setattr(
        b2_evidence_policy,
        "ensure_b2_candidate_evidence_policy_epoch",
        lambda: "b2-epoch",
    )
    monkeypatch.setattr(
        b2_evidence_policy, "attach_b2_candidate_evidence", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        vergex_policy, "ensure_vergex_utilization_policy_epoch", lambda: "vergex-epoch"
    )
    monkeypatch.setattr(
        commands,
        "_record_no_evaluable_candidate_observation",
        lambda *args, **kwargs: {"id": "zero-pending-event"},
    )

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert events == ["information_epoch"]
    assert result["information_policy_epoch_id"] == "information-epoch-id"


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
        "OII-P1-03",
        _CONTRACT,
        "P1-03 exact Serenity pending context",
        "L2b",
        "A Serenity pending carrier hydrates by opportunity_id, survives the global top-five "
        "presentation bound, scopes Layer 3 to itself, and fingerprints evidence identity.",
        status="implemented",
    )
)
def test_exact_serenity_pending_hydrates_and_scopes_its_own_research():
    from aletheia.agent_shell.trading_context import (
        build_enriched_trading_context,
        operator_decision_context_fingerprint,
        scope_operator_context_to_candidate,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision, SerenityEvidence, SerenityTheme

    target_theme = SerenityTheme(id="target-theme", name="Target", status="promoted")
    repo.insert_serenity_theme(target_theme)
    target = _serenity_opportunity(target_theme.id, "target")
    target.action_intent = "pi_evaluate"
    repo.insert_serenity_opportunity(target)
    evidence = SerenityEvidence(
        id="evidence-target-exact",
        theme_id=target_theme.id,
        title="Target filing",
        source="official",
        source_ref="https://example.com/target-filing",
        url="https://example.com/target-filing",
        tier="high_confidence",
        assessed_tier="high_confidence",
        assessed_by="deterministic_v1",
    )
    repo.insert_serenity_evidence(evidence)
    repo.insert_pi_decision(
        PiDecision(
            id="pending-target",
            action="signal_pending",
            opportunity_id=target.id,
            signal_source="serenity",
            symbol=target.symbol,
            direction=target.direction,
            market_type=target.market_type,
        )
    )
    # Newer decoys occupy the global rowid-desc top five. The exact pending target
    # must still be hydrated independently of that presentation slice.
    for index in range(5):
        theme = SerenityTheme(id=f"decoy-theme-{index}", name=f"Decoy {index}")
        repo.insert_serenity_theme(theme)
        repo.insert_serenity_opportunity(
            _serenity_opportunity(theme.id, f"decoy{index}")
        )

    context = build_enriched_trading_context(authorize_learning=False)
    scoped = scope_operator_context_to_candidate(context, target.id)
    serenity_rows = scoped["layer3"]["serenity_opportunities"]

    assert [row["id"] for row in serenity_rows] == [target.id]
    assert serenity_rows[0]["thesis"] == target.thesis
    assert serenity_rows[0]["evidence_ids"] == [evidence.id]
    assert serenity_rows[0]["score"] is not None

    first = operator_decision_context_fingerprint(scoped)
    changed = scope_operator_context_to_candidate(context, target.id)
    changed["layer3"]["serenity_opportunities"][0]["evidence_ids"] = [
        evidence.id,
        "new-evidence-identity",
    ]
    assert operator_decision_context_fingerprint(changed) != first


@spec_claim(
    SpecClaim(
        "OII-P1-04A",
        _CONTRACT,
        "P1-04 partial Serenity promotion",
        "L2b",
        "A mixed-score theme queues only qualified children, stays active while an active child "
        "has no carrier, and retries the full active sibling set without duplicates.",
        status="implemented",
    )
)
def test_mixed_theme_partial_promotion_remains_retryable(monkeypatch):
    from aletheia.agents.research_worker import _auto_promote_serenity_opportunities
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityTheme
    from aletheia.trading import serenity_scoring

    theme = SerenityTheme(id="mixed-theme", name="Mixed theme")
    repo.insert_serenity_theme(theme)
    qualified = _serenity_opportunity(theme.id, "qualified")
    waiting = _serenity_opportunity(theme.id, "waiting")
    repo.insert_serenity_opportunity(qualified)
    repo.insert_serenity_opportunity(waiting)

    scores = {qualified.id: 80, waiting.id: 20}
    monkeypatch.setattr(
        serenity_scoring,
        "score_serenity_opportunity",
        lambda opportunity, **_kwargs: {"score": scores[opportunity["id"]]},
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon", lambda **_kwargs: None
    )
    cfg = SimpleNamespace(serenity=SimpleNamespace(min_score_for_pi=50))

    assert _auto_promote_serenity_opportunities([qualified.id], cfg) == 1
    assert repo.get_serenity_theme(theme.id)["status"] == "active"
    first = [
        row
        for row in repo.list_pi_decisions(limit=20)
        if row.get("signal_source") == "serenity"
    ]
    assert [row["opportunity_id"] for row in first] == [qualified.id]

    scores[waiting.id] = 80
    assert _auto_promote_serenity_opportunities([qualified.id], cfg) == 1
    assert repo.get_serenity_theme(theme.id)["status"] == "promoted"
    carriers = [
        row
        for row in repo.list_pi_decisions(limit=20)
        if row.get("signal_source") == "serenity"
    ]
    assert sorted(row["opportunity_id"] for row in carriers) == sorted(
        [qualified.id, waiting.id]
    )
    assert len(carriers) == 2


def test_partial_promotion_final_trade_never_requeues_after_pending_retention(
    monkeypatch,
):
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.agents.research_worker import _auto_promote_serenity_opportunities
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityTheme
    from aletheia.storage.sqlite import get_db
    from aletheia.trading import serenity_scoring

    theme = SerenityTheme(id="mixed-final-theme", name="Mixed final theme")
    repo.insert_serenity_theme(theme)
    traded = _serenity_opportunity(theme.id, "mixed-traded")
    waiting = _serenity_opportunity(theme.id, "mixed-waiting")
    repo.insert_serenity_opportunity(traded)
    repo.insert_serenity_opportunity(waiting)

    scores = {traded.id: 80, waiting.id: 20}
    score_transactions = []

    def score(opportunity, **_kwargs):
        score_transactions.append(get_db().in_transaction)
        return {"score": scores[opportunity["id"]]}

    monkeypatch.setattr(serenity_scoring, "score_serenity_opportunity", score)
    wakes = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda **kwargs: wakes.append(kwargs),
    )
    cfg = SimpleNamespace(serenity=SimpleNamespace(min_score_for_pi=50))

    assert _auto_promote_serenity_opportunities([traded.id], cfg) == 1
    claim_decision({
        "action": "trade",
        "symbol": traded.symbol,
        "direction": traded.direction,
        "market_type": traded.market_type,
        "reasoning": "durable exact trade",
        "opportunity_id": traded.id,
        "signal_source": "serenity",
    })
    get_db().execute(
        "DELETE FROM pi_decisions WHERE action='signal_pending' "
        "AND opportunity_id=? AND signal_source='serenity'",
        (traded.id,),
    )

    scores[waiting.id] = 80
    assert _auto_promote_serenity_opportunities([waiting.id], cfg) == 1
    assert _auto_promote_serenity_opportunities([waiting.id], cfg) == 0
    assert all(score_transactions), "auto-promotion scoring escaped the enqueue transaction"
    assert len(wakes) == 2
    assert repo.list_pending_pi_decisions_for_opportunity(traded.id, "serenity") == []
    assert len(
        repo.list_pending_pi_decisions_for_opportunity(waiting.id, "serenity")
    ) == 1


def test_auto_promote_notification_failure_retries_existing_carrier(monkeypatch):
    from aletheia.agents.research_worker import _auto_promote_serenity_opportunities
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityTheme
    from aletheia.trading import serenity_scoring

    theme = SerenityTheme(id="notify-retry-theme", name="Notify retry")
    repo.insert_serenity_theme(theme)
    opportunity = _serenity_opportunity(theme.id, "notify-retry")
    repo.insert_serenity_opportunity(opportunity)
    monkeypatch.setattr(
        serenity_scoring,
        "score_serenity_opportunity",
        lambda *_args, **_kwargs: {"score": 80},
    )
    attempts = []

    def notify(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RuntimeError("temporary wake failure")

    monkeypatch.setattr("aletheia.agent_runtime.pi_daemon.notify_pi_daemon", notify)
    cfg = SimpleNamespace(serenity=SimpleNamespace(min_score_for_pi=50))

    assert _auto_promote_serenity_opportunities([opportunity.id], cfg) == 1
    assert _auto_promote_serenity_opportunities([opportunity.id], cfg) == 0
    assert len(attempts) == 2
    assert len(
        repo.list_pending_pi_decisions_for_opportunity(opportunity.id, "serenity")
    ) == 1


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


def test_evidence_insert_returns_active_siblings_for_post_commit_promotion_retry(
    monkeypatch,
):
    from aletheia.agents.research_worker import _auto_promote_serenity_opportunities
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
    notify_transactions = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda **_kwargs: notify_transactions.append(get_db().in_transaction),
    )
    cfg = SimpleNamespace(serenity=SimpleNamespace(min_score_for_pi=40))

    assert retry_ids == [opportunity.id]
    assert get_db().in_transaction is False
    assert _auto_promote_serenity_opportunities(retry_ids, cfg) == 1
    assert notify_transactions == [False]


@pytest.mark.parametrize(
    ("finals", "expected_reason"),
    (
        ([{"symbol": "WRONG/USDT"}], "conflicting_final_identity"),
        ([{}, {}], "ambiguous_final_decisions"),
    ),
)
def test_non_unique_or_conflicting_serenity_final_is_typed_and_stays_queued(
    finals, expected_reason,
):
    from aletheia.agent_shell.reflection import (
        PendingSourceClaimError,
        claim_decision,
    )
    from aletheia.agent_shell.trading_context import _build_pending_signal_queue
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PiDecision, SerenityTheme

    theme = SerenityTheme(id=f"integrity-{expected_reason}", name="Integrity")
    repo.insert_serenity_theme(theme)
    opportunity = _serenity_opportunity(theme.id, f"opp-{expected_reason}")
    repo.insert_serenity_opportunity(opportunity)
    repo.enqueue_serenity_theme_for_pi(theme.id, [opportunity.id])
    for index, overrides in enumerate(finals):
        values = {
            "symbol": opportunity.symbol,
            "direction": opportunity.direction,
            "market_type": opportunity.market_type,
            **overrides,
        }
        repo.insert_pi_decision(PiDecision(
            id=f"final-{expected_reason}-{index}",
            action="skip",
            opportunity_id=opportunity.id,
            signal_source="serenity",
            **values,
        ))

    classification = repo.classify_final_pi_decision_for_opportunity(
        opportunity.id,
        "serenity",
        symbol=opportunity.symbol,
        direction=opportunity.direction,
        market_type=opportunity.market_type,
    )
    visible, queue = _build_pending_signal_queue(limit=20)

    assert classification["status"] == "integrity_unavailable"
    assert classification["reason"] == expected_reason
    [pending] = [row for row in visible if row["opportunity_id"] == opportunity.id]
    assert pending["final_integrity_status"] == "unavailable"
    assert pending["final_integrity_reason"] == expected_reason
    assert queue["unresolved_total"] >= 1
    assert repo.get_serenity_opportunity(opportunity.id)["status"] == "active"
    with pytest.raises(PendingSourceClaimError, match=expected_reason):
        claim_decision({
            "action": "skip",
            "opportunity_id": opportunity.id,
            "signal_source": "serenity",
            "symbol": opportunity.symbol,
            "direction": opportunity.direction,
            "market_type": opportunity.market_type,
            "reasoning": "must not overwrite unavailable final integrity",
        })
    assert len(
        repo.list_pending_pi_decisions_for_opportunity(opportunity.id, "serenity")
    ) == 1


@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    (
        ("missing", "serenity_opportunity_missing"),
        ("error", "serenity_context_hydration_failed"),
    ),
)
def test_exact_serenity_hydration_failure_is_typed_unavailable(
    monkeypatch, mode, expected_reason
):
    from aletheia.agent_shell import trading_context
    from aletheia.agent_shell.decision_facts import candidate_context_unavailable_reason
    from aletheia.storage import repositories as repo

    pending = {
        "opportunity_id": "serenity-unavailable",
        "signal_source": "serenity",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
    }
    layer2 = {"pending_signals": [pending]}
    layer3 = {"serenity_opportunities": []}
    if mode == "missing":
        monkeypatch.setattr(repo, "get_serenity_opportunity", lambda _id: None)
    else:
        monkeypatch.setattr(
            repo,
            "get_serenity_opportunity",
            lambda _id: {"id": "serenity-unavailable", "theme_id": "theme"},
        )
        monkeypatch.setattr(
            trading_context,
            "_serenity_context_row",
            lambda _row: (_ for _ in ()).throw(RuntimeError("read failed")),
        )

    trading_context._hydrate_pending_serenity_context(layer2, layer3)

    assert pending["serenity_context_status"] == "unavailable"
    assert pending["serenity_context_unavailable_reason"] == expected_reason
    assert layer3["serenity_opportunities"][0]["hydration_status"] == "unavailable"
    assert candidate_context_unavailable_reason(
        {"layer1": {}, "layer2": layer2}, opportunity_id=pending["opportunity_id"]
    ) == expected_reason


def test_commands_defers_typed_serenity_unavailability_before_llm(monkeypatch):
    from aletheia.agent_shell import (
        b2_evidence_policy,
        commands,
        skip_policy,
        trading_context,
        trading_decision,
        vergex_policy,
    )
    from aletheia.learning import opportunity_information_policy

    opportunity_id = "serenity-command-unavailable"
    context = {
        "layer1": {"data_health": {}, "features": [], "feature_index": {}},
        "layer2": {
            "pending_queue": {"unresolved_total": 1},
            "pending_signals": [{
                "opportunity_id": opportunity_id,
                "signal_source": "serenity",
                "symbol": "BTC/USDT",
                "direction": "long",
                "market_type": "spot",
                "serenity_context_status": "unavailable",
                "serenity_context_unavailable_reason": "serenity_opportunity_missing",
            }],
        },
        "layer3": {"serenity_opportunities": [{
            "id": opportunity_id,
            "hydration_status": "unavailable",
            "unavailable_reason": "serenity_opportunity_missing",
            "evidence_ids": [],
        }]},
    }
    monkeypatch.setattr(
        trading_context, "build_enriched_trading_context", lambda **_kwargs: context
    )
    monkeypatch.setattr(
        skip_policy, "ensure_skip_remediation_policy_epoch", lambda: "skip-epoch"
    )
    monkeypatch.setattr(skip_policy, "skip_policy_context", lambda: {
        "unchanged_context_defer_window_minutes": 120
    })
    monkeypatch.setattr(
        b2_evidence_policy,
        "ensure_b2_candidate_evidence_policy_epoch",
        lambda: "b2-epoch",
    )
    monkeypatch.setattr(
        b2_evidence_policy, "attach_b2_candidate_evidence", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        vergex_policy, "ensure_vergex_utilization_policy_epoch", lambda: "vergex-epoch"
    )
    monkeypatch.setattr(
        opportunity_information_policy,
        "ensure_opportunity_information_policy_epoch",
        lambda: "information-epoch",
    )
    monkeypatch.setattr(
        commands, "_existing_deferred_context_observation", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        commands,
        "_record_deferred_context_observation",
        lambda *_args, **_kwargs: {"id": "serenity-defer-event"},
    )

    async def fail_if_invoked(_context):
        raise AssertionError("LLM decision path must not run")

    monkeypatch.setattr(trading_decision, "evaluate_trading_opportunity", fail_if_invoked)

    result = asyncio.run(commands.evaluate_trading_opportunity({}))

    assert result["action"] == "evaluation_deferred"
    assert result["model_invoked"] is False
    assert result["defer_reason"] == "serenity_opportunity_missing"
    assert result["opportunity_id"] == opportunity_id


@spec_claim(
    SpecClaim(
        "OII-P1-05A",
        _CONTRACT,
        "P1-05 exact terminal Serenity skip",
        "L2b",
        "A final exact Serenity skip writes PiDecision and filter evidence while the writer "
        "transaction is active, then advances active to reviewed; trade leaves it active.",
        status="implemented",
    )
)
def test_terminal_serenity_skip_is_atomic_but_trade_does_not_end_source(monkeypatch):
    from aletheia.agent_shell.reflection import claim_decision
    from aletheia.analytics import filter_evidence
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityTheme
    from aletheia.storage.sqlite import get_db

    evidence_transactions = []
    monkeypatch.setattr(
        filter_evidence,
        "record_operator_filter_trace",
        lambda *_args, **_kwargs: evidence_transactions.append(get_db().in_transaction),
    )

    skip_theme = SerenityTheme(id="skip-theme", name="Skip")
    repo.insert_serenity_theme(skip_theme)
    skipped = _serenity_opportunity(skip_theme.id, "skip-child")
    repo.insert_serenity_opportunity(skipped)
    repo.enqueue_serenity_theme_for_pi(skip_theme.id, [skipped.id])
    claimed, created = claim_decision(
        {
            "action": "skip",
            "symbol": skipped.symbol,
            "direction": skipped.direction,
            "market_type": skipped.market_type,
            "reasoning": "exact adverse evidence",
            "opportunity_id": skipped.id,
            "signal_source": "serenity",
            "filter_trace": [{"filter_name": "operator_model_decision"}],
        }
    )
    assert created is True
    assert claimed["action"] == "skip"
    assert evidence_transactions == [True]
    assert repo.get_serenity_opportunity(skipped.id)["status"] == "reviewed"

    trade_theme = SerenityTheme(id="trade-theme", name="Trade")
    repo.insert_serenity_theme(trade_theme)
    traded = _serenity_opportunity(trade_theme.id, "trade-child")
    repo.insert_serenity_opportunity(traded)
    repo.enqueue_serenity_theme_for_pi(trade_theme.id, [traded.id])
    claim_decision(
        {
            "action": "trade",
            "symbol": traded.symbol,
            "direction": traded.direction,
            "market_type": traded.market_type,
            "reasoning": "trade remains live research",
            "opportunity_id": traded.id,
            "signal_source": "serenity",
        }
    )
    assert repo.get_serenity_opportunity(traded.id)["status"] == "active"


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


def test_serenity_prompt_display_is_bounded_without_losing_fingerprint_facts(monkeypatch):
    from aletheia.agent_shell.trading_context import (
        _serenity_context_row,
        operator_decision_context_fingerprint,
    )
    from aletheia.agent_shell.trading_decision import _build_assessment_prompt
    from aletheia.storage import repositories as repo
    from aletheia.trading import serenity_scoring

    thesis_tail = "TAIL-THESIS-FACT"
    full_thesis = ("thesis " * 1000) + thesis_tail
    evidence_tail = "TAIL-EVIDENCE-FACT"
    opportunity = {
        "id": "serenity-large-prompt",
        "theme_id": "serenity-large-theme",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "thesis": full_thesis,
        "assessed_tier": "likely",
        "researchability_grade": "B",
        "asset_class": "crypto",
    }
    evidence = [
        {
            "id": f"large-evidence-{index}",
            "source_ref": "https://example.com/" + ("x" * 200) + str(index),
            "title": f"Evidence {index}",
            "supports": True,
        }
        for index in range(30)
    ]
    evidence[-1]["source_ref"] += evidence_tail
    monkeypatch.setattr(repo, "list_serenity_evidence", lambda **_kwargs: evidence)
    monkeypatch.setattr(repo, "list_serenity_risk_gates", lambda **_kwargs: [])
    monkeypatch.setattr(
        serenity_scoring,
        "score_serenity_opportunity",
        lambda *_args, **_kwargs: {
            "score": 60,
            "bucket": "research_priority",
            "evidence_count": 30,
        },
    )

    row = _serenity_context_row(opportunity)
    context = {
        "layer1": {},
        "layer2": {},
        "layer3": {"serenity_opportunities": [row]},
    }
    prompt = _build_assessment_prompt(context)

    assert row["thesis"] == full_thesis
    assert row["key_evidence"].endswith(evidence_tail)
    assert len(row["thesis_display"]) < len(full_thesis)
    assert len(row["key_evidence_display"]) < len(row["key_evidence"])
    assert thesis_tail not in prompt
    assert evidence_tail not in prompt
    assert "display bounded" in prompt

    first = operator_decision_context_fingerprint(context)
    row["thesis"] = full_thesis.replace(thesis_tail, "DIFFERENT-PERSISTED-TAIL")
    assert operator_decision_context_fingerprint(context) != first
