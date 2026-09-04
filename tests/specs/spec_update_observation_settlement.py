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
    test_update_progress_separates_stop_settlement_backup_and_keeps_lock as _assert_phase_boundary,
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
    _assert_fast_path(tmp_path, monkeypatch)


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
    _assert_minimal_cut(tmp_path, monkeypatch)


@spec_claim(SpecClaim(
    "OBC-UPD-03",
    _CONTRACT,
    "Update activation cut",
    "L2a",
    "The Observation cycle lock remains held across the minimal settlement, "
    "authoritative backup, source activation and target checkpoint re-anchor.",
))
def test_activation_cut_lock_covers_backup_and_source_switch(tmp_path, monkeypatch):
    _assert_lock_lifetime(monkeypatch)
    _assert_phase_boundary(tmp_path, monkeypatch)
