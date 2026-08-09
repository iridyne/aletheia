"""Focused tests for the Risk Engine causal gate wiring (trust boundary).

Regression context: `_evaluate_proposal_locked` used to build the causal input
dict from fields that do not exist on TradeProposal (thesis/confidence/...),
so the AttributeError was silently swallowed and `check_trade_causality` never
ran. These tests lock the repaired wiring:

1. facts are resolved from the linked Opportunity and reach the causal check
2. insufficient evidence / ambiguous opportunity safely skips (no causal_*)
3. paper/demo failures stay advisory (approved unaffected, message summary)
4. live failures hard-reject with `causal_check_failed:*` (trust §6.3 dual path)
5. the fact resolver tolerates malformed JSON / missing metadata
"""

from __future__ import annotations

import json

import pytest

from aletheia.portfolio.account_loss import AccountLossSnapshot


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    import sqlite3

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    return conn


def _wire_engine_defaults(monkeypatch):
    """Neutralize environment-dependent checks so causal wiring is isolated."""
    import aletheia.risk.engine as eng
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Paper"
    monkeypatch.setattr(eng, "load_config", lambda: cfg)
    monkeypatch.setattr(eng, "_portfolio_drawdown_scale", lambda _mode: 1.0)
    monkeypatch.setattr(eng, "_check_lesson_constraints", lambda _proposal: [])
    monkeypatch.setattr(eng, "_lookup_beta", lambda _symbol: None)
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )
    monkeypatch.setattr(
        eng,
        "authoritative_account_realized_loss",
        lambda _mode: AccountLossSnapshot(
            available=True,
            realized_pnl_usdt=0.0,
            loss_usdt=0.0,
            source="isolated_test",
            reason="available",
            event_count=0,
        ),
    )
    return eng


def _install_causal_spy(monkeypatch):
    """Wrap the real check_trade_causality, recording every input it receives."""
    from aletheia.risk import causal as causal_mod

    calls: list[dict] = []
    real = causal_mod.check_trade_causality

    def _spy(proposal_data, market_context=None):
        calls.append(dict(proposal_data))
        return real(proposal_data, market_context)

    monkeypatch.setattr(causal_mod, "check_trade_causality", _spy)
    return calls


def _insert_rich_opportunity(repo, opportunity_id: str, **overrides):
    from aletheia.storage.models import Opportunity

    fields = dict(
        id=opportunity_id,
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="ETF inflows accelerate while funding stays flat",
        confidence="high",
        confidence_score=80,
        source_agent="market_observer",
        scanner_family="momentum",
        key_observations=json.dumps(
            [
                "ETF net inflow +$120M over 24h",
                "funding rate flat at 0.01%",
                "volume up 45% vs 20d average",
            ]
        ),
    )
    fields.update(overrides)
    opportunity = Opportunity(**fields)
    repo.insert_opportunity(opportunity)
    return opportunity


def _insert_serenity_theme(repo, theme_id: str):
    from aletheia.storage.models import SerenityTheme

    repo.insert_serenity_theme(SerenityTheme(id=theme_id, name="causal test theme"))


def _make_paper_proposal(repo, proposal_id: str, opportunity_id: str):
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id=proposal_id,
        opportunity_id=opportunity_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    return proposal


def test_causal_gate_receives_opportunity_facts(tmp_path, monkeypatch):
    """The causal check must run and receive facts resolved from the Opportunity."""
    _setup_db(tmp_path, monkeypatch)
    eng = _wire_engine_defaults(monkeypatch)
    calls = _install_causal_spy(monkeypatch)
    from aletheia.storage import repositories as repo

    _insert_rich_opportunity(repo, "causal-wired-opportunity")
    proposal = _make_paper_proposal(
        repo, "causal-wired-proposal", "causal-wired-opportunity"
    )

    result = eng.evaluate_proposal(proposal, decision_role="authoritative")

    assert len(calls) == 1, (
        "check_trade_causality was never invoked — causal gate is not wired"
    )
    payload = calls[0]
    assert payload["direction"] == "long"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["thesis"] == "ETF inflows accelerate while funding stays flat"
    assert payload["confidence"] == "high"
    assert payload["confidence_score"] == 80
    assert payload["scanner_family"] == "momentum"
    assert isinstance(payload["key_observations"], list)
    assert len(payload["key_observations"]) == 3
    # Rich facts pass the gate; no causal violation may appear.
    assert result.approved, result.violations
    assert not any(v.startswith("causal_check_failed") for v in result.violations)


@pytest.mark.parametrize(
    "scenario",
    (
        "missing_opportunity",
        "no_thesis_no_observations",
        "ambiguous_dual_table",
    ),
)
def test_causal_gate_skips_safely_without_sufficient_facts(
    tmp_path, monkeypatch, scenario
):
    """Insufficient or ambiguous evidence must skip, never block."""
    _setup_db(tmp_path, monkeypatch)
    eng = _wire_engine_defaults(monkeypatch)
    calls = _install_causal_spy(monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, SerenityOpportunity

    if scenario == "missing_opportunity":
        # Opportunity id referenced by the proposal exists in neither table;
        # identity check 1b rejects, but the causal gate must skip silently.
        proposal = _make_paper_proposal(
            repo, "causal-skip-missing", "ghost-opportunity"
        )
    elif scenario == "no_thesis_no_observations":
        _insert_rich_opportunity(
            repo,
            "causal-skip-empty-opportunity",
            thesis=None,
            key_observations="",
        )
        proposal = _make_paper_proposal(
            repo, "causal-skip-empty", "causal-skip-empty-opportunity"
        )
    else:
        # Same id present in BOTH opportunity tables → ambiguous source → skip.
        opportunity = _insert_rich_opportunity(repo, "causal-skip-dual")
        _insert_serenity_theme(repo, "causal-skip-theme")
        repo.insert_serenity_opportunity(
            SerenityOpportunity(
                id=opportunity.id,
                theme_id="causal-skip-theme",
                symbol="BTC/USDT",
                direction="long",
                thesis=opportunity.thesis,
            )
        )
        proposal = _make_paper_proposal(repo, "causal-skip-dual-proposal", opportunity.id)

    result = eng.evaluate_proposal(proposal, decision_role="authoritative")

    assert calls == [], "causal check must not run without sufficient facts"
    assert not any(v.startswith("causal_") for v in result.violations), (
        result.violations
    )


@pytest.mark.parametrize("execution_mode", ("paper_internal", "bitget_live_limited"))
def test_thesis_only_proposal_is_skipped_in_every_mode(tmp_path, monkeypatch, execution_mode):
    """Thesis without observations can never pass the scorer: skip, never reject.

    check_trade_causality derives its score solely from key_observations
    (committee_report is not persisted yet), so a thesis-only proposal would
    deterministically fail. The skip condition must stay aligned with the
    scorer's actual inputs — in live mode this prevents mass hard-rejection of
    thin-data proposals.
    """
    _setup_db(tmp_path, monkeypatch)
    eng = _wire_engine_defaults(monkeypatch)
    calls = _install_causal_spy(monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    _insert_rich_opportunity(
        repo,
        "causal-thesis-only-opportunity",
        thesis="A well-written thesis but zero cited observations",
        confidence="speculative",
        confidence_score=30,
        key_observations=json.dumps([]),
    )
    proposal = TradeProposal(
        id=f"causal-thesis-only-proposal-{execution_mode}",
        opportunity_id="causal-thesis-only-opportunity",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode=execution_mode,
    )
    repo.insert_trade_proposal(proposal)

    result = eng.evaluate_proposal(proposal, decision_role="authoritative")

    assert calls == [], "thesis-only evidence must skip the causal check"
    assert not any(v.startswith("causal_") for v in result.violations), (
        result.violations
    )


def test_paper_causal_failure_is_advisory_only(tmp_path, monkeypatch):
    """paper_internal: failing causal facts annotate, they never reject."""
    db = _setup_db(tmp_path, monkeypatch)
    eng = _wire_engine_defaults(monkeypatch)
    calls = _install_causal_spy(monkeypatch)
    from aletheia.storage import repositories as repo

    # Facts sufficient (2 observations) but too thin to pass: technical score
    # 0.1 < 0.2 → cause_type="unknown" → check deterministically fails. Zero
    # observations would skip instead (thesis-only is insufficient evidence).
    _insert_rich_opportunity(
        repo,
        "causal-paper-weak-opportunity",
        thesis="A vague hunch about momentum continuation",
        confidence="speculative",
        confidence_score=30,
        key_observations=json.dumps(
            ["price above 20d average", "single unconfirmed indicator tick"]
        ),
    )
    proposal = _make_paper_proposal(
        repo, "causal-paper-weak-proposal", "causal-paper-weak-opportunity"
    )

    result = eng.evaluate_proposal(proposal, decision_role="authoritative")

    assert len(calls) == 1
    assert not calls[0]["passed"] if hasattr(calls[0], "passed") else True
    assert result.approved, result.violations
    assert not any(v.startswith("causal_check_failed") for v in result.violations)
    assert "causal:" in result.message

    row = db.execute(
        "SELECT message FROM risk_decisions WHERE proposal_id=? ORDER BY created_at DESC",
        (proposal.id,),
    ).fetchone()
    assert row is not None
    assert "causal:" in (row["message"] or "")


def test_live_causal_failure_rejects(tmp_path, monkeypatch):
    """live: failing causal facts must hard-reject (trust §6.3 rejected path)."""
    _setup_db(tmp_path, monkeypatch)
    eng = _wire_engine_defaults(monkeypatch)
    calls = _install_causal_spy(monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    # 2 observations → the gate runs but scores 0.1 (< 0.2) → fails → live rejects.
    _insert_rich_opportunity(
        repo,
        "causal-live-weak-opportunity",
        thesis="A vague hunch about momentum continuation",
        confidence="speculative",
        confidence_score=30,
        key_observations=json.dumps(
            ["price above 20d average", "single unconfirmed indicator tick"]
        ),
    )
    proposal = TradeProposal(
        id="causal-live-weak-proposal",
        opportunity_id="causal-live-weak-opportunity",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="bitget_live_limited",
    )
    repo.insert_trade_proposal(proposal)

    result = eng.evaluate_proposal(proposal, decision_role="authoritative")

    assert len(calls) == 1
    assert not result.approved
    assert any(v.startswith("causal_check_failed:") for v in result.violations), (
        result.violations
    )


def test_resolve_causal_facts_tolerates_malformed_json(tmp_path, monkeypatch):
    """The resolver never raises on bad key_observations JSON or missing metadata."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.risk.causal import resolve_causal_facts
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    _insert_rich_opportunity(
        repo,
        "causal-malformed-opportunity",
        thesis="thesis present",
        key_observations="{not valid json",
    )
    proposal = _make_paper_proposal(
        repo, "causal-malformed-proposal", "causal-malformed-opportunity"
    )

    facts = resolve_causal_facts(proposal)

    assert facts is not None
    assert facts["key_observations"] == []
    assert facts["thesis"] == "thesis present"
    assert facts["committee_report"] is None

    # Non-list JSON payloads are equally tolerated.
    _insert_rich_opportunity(
        repo,
        "causal-nonlist-opportunity",
        thesis="thesis present",
        key_observations=json.dumps({"unexpected": "shape"}),
    )
    proposal2 = _make_paper_proposal(
        repo, "causal-nonlist-proposal", "causal-nonlist-opportunity"
    )
    facts2 = resolve_causal_facts(proposal2)
    assert facts2 is not None
    assert facts2["key_observations"] == []

    # No opportunity_id → None, no exception.
    detached = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    assert resolve_causal_facts(detached) is None


def test_resolve_causal_facts_maps_serenity_source(tmp_path, monkeypatch):
    """Serenity opportunities map thesis/confidence_score; observations default empty."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.risk.causal import resolve_causal_facts
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityOpportunity, TradeProposal

    _insert_serenity_theme(repo, "causal-serenity-theme")
    repo.insert_serenity_opportunity(
        SerenityOpportunity(
            id="causal-serenity-opportunity",
            theme_id="causal-serenity-theme",
            symbol="BTC/USDT",
            direction="long",
            thesis="Serenity theme thesis",
            confidence_score=55,
        )
    )
    proposal = TradeProposal(
        id="causal-serenity-proposal",
        opportunity_id="causal-serenity-opportunity",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    facts = resolve_causal_facts(proposal)

    assert facts is not None
    assert facts["thesis"] == "Serenity theme thesis"
    assert facts["confidence_score"] == 55
    assert facts["key_observations"] == []
    assert facts["direction"] == "long"


# ---------------------------------------------------------------------------
# Committee report consumption (explainability wiring, read-only advisory)
# ---------------------------------------------------------------------------


def test_resolve_causal_facts_includes_persisted_committee_report(tmp_path, monkeypatch):
    """A persisted committee evaluation must reach the causal gate.

    Before persistence existed, resolve_causal_facts hard-coded
    committee_report=None; the causal scorer's committee branch was dead code.
    """
    _setup_db(tmp_path, monkeypatch)
    from aletheia.risk.causal import resolve_causal_facts
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import CommitteeReport, Opportunity, TradeProposal

    oid = "opp-comm-causal1"
    repo.insert_opportunity(
        Opportunity(
            id=oid, symbol="BTC/USDT", market="spot", thesis="t",
            confidence="medium_confidence",
        )
    )
    repo.insert_committee_report(
        CommitteeReport(
            opportunity_id=oid,
            symbol="BTC/USDT",
            decision_id="dec-1",
            direction="long",
            confidence=0.62,
            explainability_score=0.71,
            agent_opinions_json=json.dumps([{
                "agent": "LLM",
                "direction": "long",
                "confidence": 0.62,
                "reasoning": "volume expansion",
                "evidence": [{"indicator": "RSI", "value": "RSI 48", "contribution": 10.0}],
            }]),
        )
    )
    proposal = TradeProposal(
        id="prop-comm-causal1", opportunity_id=oid, symbol="BTC/USDT",
        market_type="spot", direction="long", quantity=1.0, price=100.0,
    )

    facts = resolve_causal_facts(proposal)

    assert facts is not None
    cr = facts["committee_report"]
    assert cr is not None, "persisted committee report must reach the causal gate"
    assert cr["explainability_score"] == 0.71
    assert cr["agent_opinions"][0]["agent"] == "LLM"
    assert len(cr["agent_opinions"][0]["evidence"]) == 1


def test_resolve_causal_facts_without_committee_report_stays_none(tmp_path, monkeypatch):
    """No persisted report → committee_report stays None (pre-committee
    behavior preserved; the gate skips/advisories exactly as before)."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.risk.causal import resolve_causal_facts
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    oid = "opp-comm-causal2"
    repo.insert_opportunity(
        Opportunity(
            id=oid, symbol="BTC/USDT", market="spot", thesis="t",
            confidence="medium_confidence",
        )
    )
    proposal = TradeProposal(
        id="prop-comm-causal2", opportunity_id=oid, symbol="BTC/USDT",
        market_type="spot", direction="long", quantity=1.0, price=100.0,
    )

    facts = resolve_causal_facts(proposal)

    assert facts is not None
    assert facts["committee_report"] is None


def test_check_trade_causality_uses_committee_explainability_score():
    """High ES adds evidence; low ES adds a concern (scorer contract)."""
    from aletheia.risk.causal import check_trade_causality

    base = {
        "direction": "long",
        "symbol": "BTC/USDT",
        "thesis": "momentum",
        "confidence": "medium",
        "confidence_score": 60,
        "key_observations": ["a", "b", "c"],
    }
    high_es = check_trade_causality({
        **base,
        "committee_report": {
            "agent_opinions": [],
            "explainability_score": 0.8,
        },
    })
    assert any("可解释性" in e for e in high_es.evidence)

    low_es = check_trade_causality({
        **base,
        "committee_report": {
            "agent_opinions": [],
            "explainability_score": 0.2,
        },
    })
    assert any("可解释性" in c for c in low_es.concerns)
