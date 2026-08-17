"""Property-based invariants for profit-gradient evidence primitives.

``aletheia/analytics/profit_gradient_evidence.py`` produces the signed
audit trail that gates profit-loop attribution. Its correctness rests on
four pure primitives whose invariants line coverage cannot see:

1. **``_epoch_hash`` is a deterministic function of policy content.** A
   restart, a re-serialisation, or a change in dict order must not
   rotate the hash. Otherwise the same policy epoch cannot be recognised
   across the cutoff, and evidence identity breaks.

2. **``_epoch_hash`` ignores ``code_commit`` at the top level.** The
   commit is recorded on the exposure row, not baked into epoch
   identity. This mirrors the guarantee in
   ``learning/policy_epochs._without_code_commit`` and must not drift.

3. **``_hash`` / ``_json_value`` are canonical.** Same content — even
   with sets, nested dicts, datetimes, NaN, or a different Python-level
   insertion order — must produce the same digest. This is what makes
   the evidence hash stable across builds.

4. **``_redact`` scrubs secret-shaped keys at every depth.** Any key whose
   case/separator-normalized form contains one of the documented fragments must
   be replaced with ``"[redacted]"``, whether it appears at the top
   level, inside a nested dict, or inside a list of dicts. This is the
   audit-safety invariant of the evidence pipeline.

5. **``_parse_utc`` fails closed on malformed timestamps.** Every path
   that parses a cutoff or started_at relies on this: silent success
   with a naive datetime would let a cutoff move without the audit
   trail catching it (see the fix in PR #978).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, strategies as st

from aletheia.config.secrets import is_secret_key
from aletheia.analytics.profit_gradient_evidence import (
    _SECRET_FRAGMENTS,
    _epoch_hash,
    _finite,
    _hash,
    _json_value,
    _parse_utc,
    _redact,
)


# --------------------------------------------------------------------------- #
# _epoch_hash — determinism and semantic salt
# --------------------------------------------------------------------------- #


_scalar = st.one_of(
    st.text(max_size=16),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.booleans(),
    st.none(),
)


def _effective_values() -> st.SearchStrategy[dict]:
    """Flat effective-values dict; keys avoid secret fragments and
    ``code_commit`` (tested separately)."""
    key = st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=8,
    ).filter(
        lambda k: k != "code_commit"
        and not is_secret_key(k)
    )
    return st.dictionaries(key, _scalar, max_size=6)


_path_ids = st.lists(
    st.text(
        alphabet=st.characters(min_codepoint=97, max_codepoint=122),
        min_size=1,
        max_size=8,
    ),
    max_size=4,
)


@given(values=_effective_values(), paths=_path_ids, safety=st.booleans())
@settings(max_examples=200, deadline=None)
def test_epoch_hash_is_dict_insertion_order_independent(
    values: dict, paths: list[str], safety: bool
) -> None:
    """Reversing the insertion order of the effective-values dict must
    not rotate the epoch hash."""
    reversed_values = dict(reversed(list(values.items())))
    h1 = _epoch_hash(
        epoch_kind="test",
        effective_values=values,
        affected_path_ids=paths,
        safety_required=safety,
    )
    h2 = _epoch_hash(
        epoch_kind="test",
        effective_values=reversed_values,
        affected_path_ids=paths,
        safety_required=safety,
    )
    assert h1 == h2


@given(values=_effective_values(), paths=_path_ids, safety=st.booleans())
@settings(max_examples=200, deadline=None)
def test_epoch_hash_is_path_id_order_and_duplicate_independent(
    values: dict, paths: list[str], safety: bool
) -> None:
    """affected_path_ids is a set in disguise: order and duplicates must
    not affect the hash."""
    h1 = _epoch_hash(
        epoch_kind="test",
        effective_values=values,
        affected_path_ids=paths,
        safety_required=safety,
    )
    h2 = _epoch_hash(
        epoch_kind="test",
        effective_values=values,
        affected_path_ids=list(reversed(paths)) + paths,
        safety_required=safety,
    )
    assert h1 == h2


@given(values=_effective_values(), commit=st.text(min_size=1, max_size=12))
@settings(max_examples=200, deadline=None)
def test_epoch_hash_ignores_top_level_code_commit(
    values: dict, commit: str
) -> None:
    """A rebuild that changes only the deployed commit must NOT rotate
    the epoch hash — matches the ``_without_code_commit`` guarantee in
    ``learning/policy_epochs`` so both hashers agree."""
    if "code_commit" in values:
        return
    with_commit = {**values, "code_commit": commit}
    h_clean = _epoch_hash(
        epoch_kind="test",
        effective_values=values,
        affected_path_ids=[],
        safety_required=False,
    )
    h_with = _epoch_hash(
        epoch_kind="test",
        effective_values=with_commit,
        affected_path_ids=[],
        safety_required=False,
    )
    assert h_clean == h_with


def test_epoch_hash_is_salted_by_kind_paths_and_safety() -> None:
    """Any of ``epoch_kind``, ``affected_path_ids`` (as a set), or
    ``safety_required`` changing must rotate the hash. Otherwise a
    safety-required change would silently reuse a treatment epoch."""
    baseline = _epoch_hash(
        epoch_kind="k1",
        effective_values={"a": 1},
        affected_path_ids=["p"],
        safety_required=False,
    )
    kind = _epoch_hash(
        epoch_kind="k2",
        effective_values={"a": 1},
        affected_path_ids=["p"],
        safety_required=False,
    )
    paths = _epoch_hash(
        epoch_kind="k1",
        effective_values={"a": 1},
        affected_path_ids=["q"],
        safety_required=False,
    )
    safety = _epoch_hash(
        epoch_kind="k1",
        effective_values={"a": 1},
        affected_path_ids=["p"],
        safety_required=True,
    )
    assert baseline != kind
    assert baseline != paths
    assert baseline != safety


# --------------------------------------------------------------------------- #
# _hash / _json_value — canonicalisation
# --------------------------------------------------------------------------- #


@given(payload=_effective_values())
@settings(max_examples=200, deadline=None)
def test_hash_is_stable_across_reserialisation(payload: dict) -> None:
    """A dict fed twice — once directly, once after being round-tripped
    through ``_json_value`` — must hash to the same value. This is the
    property downstream consumers depend on to compare evidence hashes
    across code paths that touch the values in different orders."""
    once = _hash(payload)
    twice = _hash(_json_value(payload))
    assert once == twice


def test_hash_treats_sets_and_lists_as_the_same_content() -> None:
    """``_json_value`` normalises sets to sorted lists so a caller can
    build the same evidence body with either representation without
    changing the digest."""
    from_set = _hash({"paths": {"a", "b", "c"}})
    from_list = _hash({"paths": ["a", "b", "c"]})
    from_reversed = _hash({"paths": ["c", "b", "a"]})
    assert from_set == from_list
    # Note: the module only sorts *sets*, not lists. Lists retain their
    # order (which is correct — a list is meaningful order-carrying data).
    assert from_list != from_reversed


def test_hash_survives_non_finite_floats() -> None:
    """NaN and infinity must be preserved as tagged sentinels so the
    digest still stabilises. The alternative — silent conversion or
    exception — would either lose evidence identity or crash the
    evidence writer on a legitimate metric outlier."""
    payload = {"delta": float("nan"), "bound": float("inf")}
    # No exception; deterministic across calls.
    assert _hash(payload) == _hash(payload)


@given(payload=_effective_values())
@settings(max_examples=200, deadline=None)
def test_hash_changes_when_a_top_level_value_changes(payload: dict) -> None:
    """A one-value change must change the digest. Otherwise the hash is
    not doing its evidence-identity job."""
    baseline = _hash(payload)
    sentinel_key = "__sentinel__"
    mutated = {**payload, sentinel_key: "unique-value"}
    assert baseline != _hash(mutated)


# --------------------------------------------------------------------------- #
# _redact — secret scrubbing at every depth
# --------------------------------------------------------------------------- #


@given(fragment=st.sampled_from(_SECRET_FRAGMENTS), payload=_effective_values())
@settings(max_examples=200, deadline=None)
def test_redact_scrubs_secret_shaped_keys_at_any_depth(
    fragment: str, payload: dict
) -> None:
    """A secret-shaped key at the top level, inside a nested dict, or
    inside a list-of-dicts must all be redacted to the exact string
    ``"[redacted]"``. This is the audit-safety invariant of the
    evidence pipeline."""
    poisoned_key = f"APP_{fragment}_KEY"
    victim = "the-secret-value"
    nested = {
        "outer": {
            **payload,
            poisoned_key: victim,
            "child": {poisoned_key: victim},
        },
        "list": [{poisoned_key: victim}, {"safe": victim}],
    }
    redacted = _redact(nested)
    # Walk every position where we planted the victim; each must be [redacted].
    assert redacted["outer"][poisoned_key] == "[redacted]"
    assert redacted["outer"]["child"][poisoned_key] == "[redacted]"
    assert redacted["list"][0][poisoned_key] == "[redacted]"
    # A non-secret-shaped sibling key keeps its value.
    assert redacted["list"][1]["safe"] == victim


def test_redact_is_case_insensitive_on_fragment_match() -> None:
    """Casing does not affect the shared secret-key classifier."""
    for key in ("API_KEY", "api_key", "my_api_key", "MY_TOKEN", "TOKEN"):
        result = _redact({key: "s3cret"})
        assert result[key] == "[redacted]", key


@given(
    base=st.sampled_from(("api_key", "private_key")),
    separator=st.sampled_from(("", "_", "-", ".", " ")),
    casing=st.sampled_from(("lower", "upper", "title")),
)
@settings(max_examples=30, deadline=None)
def test_redact_scrubs_case_and_separator_variants(
    base: str, separator: str, casing: str
) -> None:
    """API/private-key spellings cannot leak a sentinel into JSON evidence."""
    parts = base.split("_")
    key = separator.join(parts)
    if casing == "upper":
        key = key.upper()
    elif casing == "title":
        key = separator.join(part.title() for part in parts)

    redacted = _redact({"nested": [{key: "issue-1010-sentinel"}]})
    assert redacted["nested"][0][key] == "[redacted]"
    assert "issue-1010-sentinel" not in json.dumps(redacted, sort_keys=True)


@pytest.mark.parametrize("key", ("key", "monkey", "keyboard", "key_name"))
def test_redact_does_not_treat_key_alone_as_secret(key: str) -> None:
    value = "ordinary-value"
    assert _redact({key: value})[key] == value


# --------------------------------------------------------------------------- #
# _parse_utc — fail-closed timestamp parsing
# --------------------------------------------------------------------------- #


@given(
    when=st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 1, 1),
        timezones=st.just(timezone.utc),
    )
)
@settings(max_examples=100, deadline=None)
def test_parse_utc_roundtrips_iso_timestamps(when: datetime) -> None:
    """Any timezone-aware datetime must round-trip through the parser
    identically. A parser that silently normalises the value would let
    a cutoff drift."""
    parsed = _parse_utc(when.isoformat())
    assert parsed == when


@pytest.mark.parametrize(
    "bad_value",
    [
        "",
        "not-a-timestamp",
        "2025-01-01",  # missing time
        "2025-01-01T00:00:00",  # missing timezone
        "not-an-object",
        None,
        object(),
    ],
)
def test_parse_utc_rejects_malformed_and_naive_inputs(bad_value: object) -> None:
    """Empty, malformed, and naive-datetime inputs must all raise
    ``ValueError`` so the caller cannot silently proceed with a wrong
    cutoff. See PR #978 — this was a real regression fix."""
    with pytest.raises(ValueError):
        _parse_utc(bad_value)


def test_parse_utc_normalises_to_utc() -> None:
    """A non-UTC input is converted to UTC without changing the instant."""
    import datetime as _dt

    plus_eight = _dt.timezone(_dt.timedelta(hours=8))
    input_dt = _dt.datetime(2025, 1, 1, 8, 0, 0, tzinfo=plus_eight)
    parsed = _parse_utc(input_dt)
    assert parsed == _dt.datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# _finite — numeric fail-closed
# --------------------------------------------------------------------------- #


@given(value=st.floats())
@settings(max_examples=200, deadline=None)
def test_finite_returns_none_for_nan_or_infinity(value: float) -> None:
    """NaN and infinity must produce ``None`` so downstream comparisons
    never propagate an ill-defined delta."""
    if math.isfinite(value):
        assert _finite(value) == value
    else:
        assert _finite(value) is None


@given(value=st.floats(min_value=-1e6, max_value=1e6))
@settings(max_examples=200, deadline=None)
def test_finite_positive_and_nonnegative_bounds(value: float) -> None:
    """The ``positive`` and ``nonnegative`` gates must accept exactly
    their documented boundary."""
    if not math.isfinite(value):
        return
    if value > 0:
        assert _finite(value, positive=True) == value
        assert _finite(value, nonnegative=True) == value
    elif value == 0:
        assert _finite(value, positive=True) is None
        assert _finite(value, nonnegative=True) == 0.0
    else:
        assert _finite(value, positive=True) is None
        assert _finite(value, nonnegative=True) is None
