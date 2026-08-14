"""Property-based invariants for filter-replay pure kernels.

``aletheia/counterfactual/filter_replay.py`` synthesises gate-only verdicts
that gate promotion decisions. A verdict that is non-deterministic, or
that changes when the input candidate list is merely reordered, quietly
corrupts the downstream promotion register.

Line coverage cannot see either failure. These tests lock four
guarantees on the pure kernels the module docstring calls out:

1. ``_bounded_alternatives`` produces only strictly-greater, deduplicated,
   sorted, capped alternatives — regardless of caller order.
2. ``_bootstrap_delta_lcb`` is a pure deterministic function of its
   inputs and ``seed_material``; two calls with identical arguments must
   return the same number (this is what makes a replay reproducible on
   restart, and forbids Python's ``random`` state leaking in).
3. ``_outcome_metrics`` is permutation-invariant: reordering the input
   rows must not change any aggregate.
4. ``_shrunk_mean`` respects its documented boundary conditions — empty
   input returns ``None``; the shrinkage floor toward the prior is
   monotonic as ``settled_n`` grows.

The full replay pipeline is exercised by ``test_filter_replay.py``; here
we harden the arithmetic so it cannot silently rotate.
"""

from __future__ import annotations

import math

from hypothesis import HealthCheck, given, settings, strategies as st

from aletheia.counterfactual.filter_replay import (
    _MAX_ALTERNATIVES,
    _bootstrap_delta_lcb,
    _bounded_alternatives,
    _outcome_metrics,
    _shrunk_mean,
)


# --------------------------------------------------------------------------- #
# _bounded_alternatives
# --------------------------------------------------------------------------- #


# Full production TTL range: family registry uses values up to 86_400
# seconds (see aletheia/universe/allocator.py). At the boundary,
# ``_bounded_alternatives`` must return an EMPTY list because there is
# no strictly-greater TTL to consider — clamping to 86_400 must not
# fold ``current_ttl`` back onto itself.
@given(
    current_ttl=st.integers(min_value=1, max_value=86_400),
    proposals=st.lists(
        st.one_of(
            st.integers(min_value=-1000, max_value=1_000_000),
            st.text(max_size=4),  # invalid entries the sanitizer must drop
            st.none(),
        ),
        max_size=20,
    ),
)
@settings(max_examples=200, deadline=None)
def test_bounded_alternatives_are_sorted_unique_and_capped(
    current_ttl: int, proposals: list
) -> None:
    """The output is a strictly-increasing list of at most ``_MAX_ALTERNATIVES``
    integers, each greater than ``current_ttl``, and never above 86_400."""
    out = _bounded_alternatives(current_ttl, proposals)
    assert isinstance(out, list)
    assert len(out) <= _MAX_ALTERNATIVES
    assert out == sorted(set(out)), "must be sorted and unique"
    for value in out:
        assert isinstance(value, int)
        assert value > current_ttl, "must be strictly greater than current"
        assert 1 <= value <= 86_400, "must be clamped to the sanity band"


@given(
    current_ttl=st.integers(min_value=1, max_value=86_400),
    proposals=st.lists(
        st.integers(min_value=1, max_value=200_000), max_size=12
    ),
)
@settings(max_examples=100, deadline=None)
def test_bounded_alternatives_are_input_order_independent(
    current_ttl: int, proposals: list[int]
) -> None:
    """Passing the same proposals in reverse order must produce the same
    alternatives. Callers should never depend on iteration order to
    influence which TTLs are considered."""
    forward = _bounded_alternatives(current_ttl, list(proposals))
    reverse = _bounded_alternatives(current_ttl, list(reversed(proposals)))
    assert forward == reverse


def test_bounded_alternatives_uses_the_documented_fallback_ladder() -> None:
    """When no proposals are provided, the fallback ladder ``round(t*1.5),
    2t, 4t, 8t`` is the documented default. Locking this so a future
    refactor does not silently narrow the search space."""
    out = _bounded_alternatives(60, None)
    assert out == [90, 120, 240, 480]


def test_bounded_alternatives_returns_empty_at_max_ttl() -> None:
    """At the 86_400-second sanity ceiling the fallback ladder collapses
    against the clamp; the correct answer is to admit no alternatives
    rather than fold ``current_ttl`` back onto itself and produce an
    equal-TTL 'alternative'. Downstream replay must see zero candidates,
    not a spurious control-equals-treatment cohort."""
    assert _bounded_alternatives(86_400, None) == []
    # Explicit proposals above the clamp collapse the same way.
    assert _bounded_alternatives(86_400, [172_800, 200_000]) == []


# --------------------------------------------------------------------------- #
# _bootstrap_delta_lcb — determinism
# --------------------------------------------------------------------------- #


def _settled_row(net: float) -> dict:
    return {"status": "settled", "net_return_pct": net}


@st.composite
def _settled_rows(draw, min_size: int = 2, max_size: int = 20) -> list[dict]:
    values = draw(
        st.lists(
            st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
            min_size=min_size,
            max_size=max_size,
        )
    )
    return [_settled_row(v) for v in values]


@given(control=_settled_rows(), treatment=_settled_rows(), seed=st.text(min_size=1, max_size=32))
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_bootstrap_lcb_is_deterministic_in_its_inputs(
    control: list[dict], treatment: list[dict], seed: str
) -> None:
    """Two calls with identical arguments must return the same LCB. This
    forbids the bootstrap from leaking Python's global ``random`` state
    into a replay verdict."""
    a = _bootstrap_delta_lcb(control, treatment, seed_material=seed)
    b = _bootstrap_delta_lcb(control, treatment, seed_material=seed)
    assert a == b


def test_bootstrap_lcb_seed_material_shifts_result_on_high_variance_input() -> None:
    """With enough variance in the input, distinct seed material must
    shift the LCB. This proves the seed actually threads through to
    ``random.Random`` rather than being ignored — the failure mode where
    the bootstrap looks reproducible but is secretly using Python's
    global RNG would still pass ``test_bootstrap_lcb_is_deterministic``
    on a single run.
    """
    # Enough spread that the 5th-percentile bound is unlikely to quantise.
    control = [_settled_row(v) for v in (-3.0, -1.0, 0.5, 2.0, 3.5, -2.5)]
    treatment = [_settled_row(v) for v in (-1.0, 0.0, 1.5, 2.5, 4.0, 0.5)]
    a = _bootstrap_delta_lcb(control, treatment, seed_material="seed-A")
    b = _bootstrap_delta_lcb(control, treatment, seed_material="seed-B")
    assert a is not None and b is not None
    assert a != b, f"seed had no effect on the bootstrap (a={a}, b={b})"


def test_bootstrap_lcb_returns_none_on_insufficient_samples() -> None:
    """Fewer than two settled rows on either side must yield None; a
    single sample cannot produce a valid confidence bound."""
    assert _bootstrap_delta_lcb([], [_settled_row(1.0)] * 5, seed_material="s") is None
    assert _bootstrap_delta_lcb([_settled_row(1.0)], [_settled_row(1.0)] * 5, seed_material="s") is None
    assert _bootstrap_delta_lcb([_settled_row(1.0)] * 5, [], seed_material="s") is None


# --------------------------------------------------------------------------- #
# _outcome_metrics — permutation invariance
# --------------------------------------------------------------------------- #


@st.composite
def _outcome_row(draw) -> dict:
    is_settled = draw(st.booleans())
    return {
        "status": "settled" if is_settled else draw(
            st.sampled_from(["pending", "expired", "cancelled"])
        ),
        "net_return_pct": (
            draw(st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False))
            if is_settled and draw(st.booleans())
            else None
        ),
    }


@given(rows=st.lists(_outcome_row(), max_size=30))
@settings(max_examples=200, deadline=None)
def test_outcome_metrics_are_permutation_invariant(rows: list[dict]) -> None:
    """Reordering the input must not change any aggregate. The metrics
    feed downstream shrinkage and readiness gates; drift under
    permutation would leak into promotion decisions."""
    baseline = _outcome_metrics(list(rows))
    reversed_view = _outcome_metrics(list(reversed(rows)))
    for key in ("candidate_count", "settled_n", "outcome_coverage"):
        assert baseline[key] == reversed_view[key], key
    # Floats compared with tolerance because ``mean`` accumulates in a
    # different order after the reverse; behaviourally identical.
    for key in ("raw_avg_net_pct", "median_net_pct"):
        a, b = baseline[key], reversed_view[key]
        if a is None or b is None:
            assert a is None and b is None
        else:
            assert math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9), key


def test_outcome_metrics_empty_input() -> None:
    """An empty input must produce zero counts and ``None`` centres; the
    downstream ``if raw_mean is not None`` guard depends on this."""
    m = _outcome_metrics([])
    assert m == {
        "candidate_count": 0,
        "settled_n": 0,
        "outcome_coverage": 0.0,
        "raw_avg_net_pct": None,
        "median_net_pct": None,
    }


# --------------------------------------------------------------------------- #
# _shrunk_mean — boundary behaviour
# --------------------------------------------------------------------------- #


@given(
    prior=st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=50, deadline=None)
def test_shrunk_mean_returns_none_on_empty_or_unmeasured(prior: float) -> None:
    """No settled samples ⇒ None; else the shrinkage divide-by-zero
    would produce ``inf`` and silently poison downstream comparisons."""
    empty = {"settled_n": 0, "raw_avg_net_pct": None}
    unmeasured = {"settled_n": 5, "raw_avg_net_pct": None}
    assert _shrunk_mean(empty, prior) is None
    assert _shrunk_mean(unmeasured, prior) is None


@given(
    raw_mean=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    prior=st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    n_small=st.integers(min_value=1, max_value=5),
    n_large=st.integers(min_value=1_000, max_value=10_000),
)
@settings(max_examples=100, deadline=None)
def test_shrunk_mean_moves_from_prior_toward_raw_as_n_grows(
    raw_mean: float, prior: float, n_small: int, n_large: int
) -> None:
    """The shrinkage estimator must be closer to the prior when ``n`` is
    tiny, and closer to the raw sample mean when ``n`` is large. This is
    the *only* reason for shrinkage; if the direction breaks, downstream
    gates would over- or under-shrink experimental cohorts."""
    small = _shrunk_mean({"settled_n": n_small, "raw_avg_net_pct": raw_mean}, prior)
    large = _shrunk_mean({"settled_n": n_large, "raw_avg_net_pct": raw_mean}, prior)
    assert small is not None and large is not None
    # Distance to raw mean must shrink as n grows.
    assert abs(large - raw_mean) <= abs(small - raw_mean) + 1e-9
    # Distance to prior must grow as n grows (unless raw==prior).
    if not math.isclose(raw_mean, prior, abs_tol=1e-9):
        assert abs(large - prior) >= abs(small - prior) - 1e-9
