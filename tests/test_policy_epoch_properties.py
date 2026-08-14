"""Property-based invariants for policy-epoch identity.

``aletheia/learning/policy_epochs.py`` produces the deterministic hash that
every downstream evidence-cutoff / attribution query relies on. If two
semantically equal policies produce different hashes, epochs churn on
restart and observation windows fragment. If two semantically different
policies produce the same hash, learning credits the wrong build.

Line coverage cannot see either failure. These tests lock the three
guarantees the module docstring makes:

1. **Determinism.** Same effective values → same epoch hash, regardless
   of dict insertion order, nested-dict order, or list-vs-tuple in
   ``affected_path_ids``.
2. **Secret exclusion.** Any key whose lowered name contains one of the
   documented secret fragments is stripped before hashing, so credential
   leaks into config never propagate into audit rows.
3. **code_commit exclusion at top level.** A rebuild of an identical
   policy must not rotate the epoch (issue explicitly fixed in the
   module). Nested ``code_commit`` keys must be preserved so a knob
   with the same name in another config section still counts.

Also locks the two decision branches in ``compare_policy_epochs`` that
downstream attribution relies on: unchanged hash → continue; safety
change → invalidate + apply_immediately.
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from aletheia.learning.policy_epochs import (
    PolicyEpoch,
    _SECRET_FRAGMENTS,
    build_policy_epoch,
    compare_policy_epochs,
)


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

# Reasonable leaves for effective-values payloads: strings, ints, bools, None.
_leaf = st.one_of(
    st.text(max_size=16),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.booleans(),
    st.none(),
)


def _values(max_size: int = 6) -> st.SearchStrategy[dict[str, object]]:
    """Flat effective-values dicts. Keys avoid secret fragments and are
    ASCII so ``json.dumps`` behaviour is unambiguous.
    """
    key = st.text(
        alphabet=st.characters(
            min_codepoint=97, max_codepoint=122
        ),  # lowercase ascii
        min_size=1,
        max_size=8,
    ).filter(lambda k: not any(frag in k for frag in _SECRET_FRAGMENTS))
    return st.dictionaries(key, _leaf, max_size=max_size)


_path_ids = st.lists(
    st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=8,
    ),
    max_size=4,
).map(tuple)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


@given(values=_values(), paths=_path_ids, safety=st.booleans())
@settings(max_examples=200, deadline=None)
def test_epoch_hash_is_key_order_independent(
    values: dict[str, object], paths: tuple[str, ...], safety: bool
) -> None:
    """Building the epoch with reversed dict-insertion order must produce
    the same hash. Guarantees stable identity across Python versions and
    ``dict()``-vs-``json.loads()`` construction paths."""
    reversed_values = dict(reversed(list(values.items())))
    a = build_policy_epoch(
        "test_kind", values, affected_path_ids=paths, safety_required=safety
    )
    b = build_policy_epoch(
        "test_kind",
        reversed_values,
        affected_path_ids=paths,
        safety_required=safety,
    )
    assert a.epoch_hash == b.epoch_hash


@given(values=_values(), paths=_path_ids, safety=st.booleans())
@settings(max_examples=200, deadline=None)
def test_epoch_hash_is_path_id_order_and_duplicate_independent(
    values: dict[str, object], paths: tuple[str, ...], safety: bool
) -> None:
    """affected_path_ids is documented as a set: order and duplicates must
    not affect the hash. Downstream attribution treats this field as a set."""
    doubled = paths + paths
    reversed_paths = tuple(reversed(paths))
    a = build_policy_epoch(
        "test_kind", values, affected_path_ids=paths, safety_required=safety
    )
    b = build_policy_epoch(
        "test_kind", values, affected_path_ids=doubled, safety_required=safety
    )
    c = build_policy_epoch(
        "test_kind",
        values,
        affected_path_ids=reversed_paths,
        safety_required=safety,
    )
    assert a.epoch_hash == b.epoch_hash == c.epoch_hash
    # affected_path_ids is normalised to a sorted, deduplicated tuple.
    assert a.affected_path_ids == tuple(sorted(set(paths)))


# --------------------------------------------------------------------------- #
# Secret exclusion
# --------------------------------------------------------------------------- #


@given(
    base=_values(),
    fragment=st.sampled_from(_SECRET_FRAGMENTS),
    secret_value=st.text(max_size=32),
)
@settings(max_examples=200, deadline=None)
def test_epoch_hash_ignores_secret_shaped_keys(
    base: dict[str, object], fragment: str, secret_value: str
) -> None:
    """Any top-level key containing a secret fragment (case-insensitive)
    must be stripped before hashing. Otherwise, a config that
    accidentally holds a credential rotates the epoch hash whenever the
    credential rotates, and the credential enters the audit trail."""
    # Skip cases where the fragment collides with a real key in ``base``.
    if any(fragment in k.lower() for k in base):
        return
    poisoned_key = f"MyAPP_{fragment}_secret"
    poisoned = {**base, poisoned_key: secret_value}
    clean_epoch = build_policy_epoch(
        "test_kind", base, affected_path_ids=(), safety_required=False
    )
    poisoned_epoch = build_policy_epoch(
        "test_kind", poisoned, affected_path_ids=(), safety_required=False
    )
    assert clean_epoch.epoch_hash == poisoned_epoch.epoch_hash
    assert poisoned_key not in poisoned_epoch.effective_values


# --------------------------------------------------------------------------- #
# code_commit exclusion
# --------------------------------------------------------------------------- #


@given(base=_values(), commit=st.text(min_size=1, max_size=12))
@settings(max_examples=200, deadline=None)
def test_epoch_hash_ignores_top_level_code_commit(
    base: dict[str, object], commit: str
) -> None:
    """A rebuild that changes only the deployed commit must NOT rotate
    the epoch. The commit is recorded on the exposure row for
    attribution, not in the epoch identity itself."""
    if "code_commit" in base:
        return
    without = base
    with_commit = {**base, "code_commit": commit}
    a = build_policy_epoch(
        "test_kind", without, affected_path_ids=(), safety_required=False
    )
    b = build_policy_epoch(
        "test_kind", with_commit, affected_path_ids=(), safety_required=False
    )
    assert a.epoch_hash == b.epoch_hash
    # The strip is top-level only.
    assert "code_commit" not in a.effective_values
    assert "code_commit" not in b.effective_values


def test_epoch_hash_preserves_nested_code_commit() -> None:
    """A ``code_commit`` key nested inside another config section is a
    legitimate knob and must be preserved in both the effective values
    and the hash."""
    a = build_policy_epoch(
        "test_kind",
        {"deploy": {"code_commit": "aaaa"}},
        affected_path_ids=(),
        safety_required=False,
    )
    b = build_policy_epoch(
        "test_kind",
        {"deploy": {"code_commit": "bbbb"}},
        affected_path_ids=(),
        safety_required=False,
    )
    assert a.epoch_hash != b.epoch_hash
    assert a.effective_values == {"deploy": {"code_commit": "aaaa"}}


# --------------------------------------------------------------------------- #
# compare_policy_epochs — decision-branch contract
# --------------------------------------------------------------------------- #


@given(values=_values(), paths=_path_ids)
@settings(max_examples=200, deadline=None)
def test_unchanged_hash_yields_continue(
    values: dict[str, object], paths: tuple[str, ...]
) -> None:
    """Two epochs with the same hash must compare as ``continue`` /
    ``unchanged`` on every path, regardless of whether the path is
    listed as affected. Downstream attribution keys off this branch to
    decide whether a measurement window survives.
    """
    epoch = build_policy_epoch(
        "test_kind", values, affected_path_ids=paths, safety_required=False
    )
    # A random path id that may or may not be in ``paths``.
    for path_id in list(paths) + ["not-in-paths"]:
        cmp = compare_policy_epochs(epoch, epoch, path_id)
        assert cmp.experiment_action == "continue"
        assert cmp.safety_action == "unchanged"


def test_safety_change_invalidates_and_applies_immediately() -> None:
    """A safety-required change must break credit for learning and apply
    immediately: this is the module's contract with the risk engine.
    """
    before = build_policy_epoch(
        "test_kind",
        {"knob": "a"},
        affected_path_ids=("p1",),
        safety_required=False,
    )
    after = build_policy_epoch(
        "test_kind",
        {"knob": "b"},
        affected_path_ids=("p1",),
        safety_required=True,
    )
    cmp = compare_policy_epochs(before, after, "p1")
    assert cmp.experiment_action == "invalidate"
    assert cmp.safety_action == "apply_immediately"


def test_unaffected_path_is_unchanged_even_with_new_hash() -> None:
    """A policy change on a path we're not observing must not disrupt the
    observation. Otherwise every unrelated knob rotates every path's
    epoch."""
    before = build_policy_epoch(
        "test_kind",
        {"knob": "a"},
        affected_path_ids=("p1",),
        safety_required=False,
    )
    after = build_policy_epoch(
        "test_kind",
        {"knob": "b"},
        affected_path_ids=("p1",),
        safety_required=False,
    )
    cmp = compare_policy_epochs(before, after, "p2")
    assert cmp.experiment_action == "continue"
    assert cmp.safety_action == "unchanged"
    assert cmp.reason == "path_not_affected"
