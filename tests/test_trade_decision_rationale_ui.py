"""Frontend contract for persisted trade entry/exit explanations."""

from pathlib import Path


def test_causal_chain_renders_persisted_entry_and_exit_reasoning_honestly():
    source = Path("frontend/src/components/CausalChain.tsx").read_text(encoding="utf-8")

    assert 'stage="Entry Reasoning"' in source
    assert "entry_parameters" in source
    assert 'stage="Close Reasoning"' in source
    assert "exit_reasoning" in source
    assert "not recorded" in source
