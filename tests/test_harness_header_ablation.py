from scripts.harness_header_ablation import CALLERS, HEADER, _compliant, _facts


def test_corpus_has_the_five_preregistered_callers():
    assert set(CALLERS) == {
        "market_observer", "pi_trading_decision", "risk_critic", "research_worker", "dreaming_agent"
    }


def test_synthetic_facts_are_deterministic_and_non_secret():
    assert _facts(7) == _facts(7)
    assert "api" not in str(_facts(7)).lower()


def test_header_is_context_only_and_blocks_no_tools():
    assert "[Caller system prompt follows]" in HEADER
    assert "no:live_order" in HEADER
    assert "ignore" not in HEADER.lower()


def test_compliance_checks_structured_and_text_outputs():
    assert _compliant(
        '{"action":"skip","direction":"neutral","conviction":40,"reasoning":"mixed"}',
        CALLERS["pi_trading_decision"],
    )
    assert not _compliant("not json", CALLERS["pi_trading_decision"])
    assert _compliant("Concise advisory text.", CALLERS["risk_critic"])
