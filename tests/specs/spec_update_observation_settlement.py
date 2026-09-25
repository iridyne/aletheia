"""Executable contract for the minimal update-time Observation cut."""

from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.test_update_settlement_hot_path import (
    test_caught_up_update_settlement_is_a_true_read_only_fast_path as _assert_fast_path,
)
from tests.test_update_settlement_hot_path import (
    test_update_barrier_keeps_cycle_lock_around_minimal_cut as _assert_lock_lifetime,
)
from tests.test_update_settlement_hot_path import (
    test_update_progress_separates_stop_settlement_and_keeps_lock as _assert_phase_boundary,
)
from tests.test_update_settlement_hot_path import (
    test_update_settlement_drains_only_delivery_then_strictly_verifies as _assert_minimal_cut,
)


_CONTRACT = "observation_lab_backup_contract"


@spec_claim(SpecClaim(
    "OBC-UPD-01",
    _CONTRACT,
    "Update activation cut",
    "L2a",
    "A caught-up, strictly matching delivery frontier is a read-only settlement: "
    "no importer, maintenance pass or checkpoint rewrite is permitted.",
))
def test_caught_up_activation_cut_is_a_true_no_op(tmp_path, monkeypatch):
    result = _assert_fast_path(tmp_path, monkeypatch)
    assert result is not None and result.get("fast_path") is True, (
        "caught-up activation cut must take the read-only fast path"
    )


@spec_claim(SpecClaim(
    "OBC-UPD-02",
    _CONTRACT,
    "Update activation cut",
    "L2a",
    "When receipts are pending, update settlement drains only the contiguous "
    "delivery frontier and establishes a strict checkpoint; audit mirroring, "
    "pruning and compaction remain ordinary maintenance.",
))
def test_activation_cut_contains_only_delivery_and_checkpoint_work(
    tmp_path, monkeypatch,
):
    result = _assert_minimal_cut(tmp_path, monkeypatch)
    assert result is not None and result.get("delivery_import", {}).get("processed", 0) > 0, (
        "activation cut must drain the delivery frontier"
    )


@spec_claim(SpecClaim(
    "OBC-UPD-03",
    _CONTRACT,
    "Update activation cut",
    "L2a",
    "The Observation cycle lock remains held across the minimal settlement, "
    "authoritative backup, source activation and target checkpoint re-anchor.",
))
def test_activation_cut_lock_covers_backup_and_source_switch(tmp_path, monkeypatch):
    lock_result = _assert_lock_lifetime(monkeypatch)
    phase_result = _assert_phase_boundary(tmp_path, monkeypatch)
    assert lock_result is not None, "cycle lock must remain held across the minimal cut"
    assert phase_result is not None and phase_result.get("continuity_verified") is True, (
        "settlement barrier must cover backup and source switch"
    )


@spec_claim(SpecClaim(
    "OBC-UPD-04", _CONTRACT, "Bounded applied delivery recovery (#3096)", "L2a",
    "Only an exact reconstructed prior Lab and unclassified applied receipt suffix may settle; "
    "altered evidence or retry state cannot authorize a checkpoint.",
))
def test_bounded_applied_delivery_recovery():
    from tests.test_update_delivery_recovery import test_only_exact_unclassified_delivery_suffix_can_settle
    proof = test_only_exact_unclassified_delivery_suffix_can_settle(None)
    assert proof is not None and proof.get("ok") is True, (
        "clean applied-delivery suffix must settle with a proven checkpoint"
    )
