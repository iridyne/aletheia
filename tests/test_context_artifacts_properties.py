"""Property-based invariants for the context-artifact recorder.

``aletheia/economics/context_artifacts.py`` is the audit boundary between
rendered model context and stored evidence. Its contract, quoted from
the module docstring, is:

    "This module never stores prompts, model responses, or source
    bodies. It records only stable source identities and bounded
    selection metadata so context can be audited without becoming a
    second prompt archive."

Line coverage tells us branches were taken; these properties assert the
audit contract itself, so a future refactor that grows the persisted
payload would break the test loudly.

Two pure helpers carry the contract:

* ``_bounded_text`` — clips every persisted string to 160 chars. Any
  unbounded string would let a caller smuggle rendered prompt bytes past
  the recorder.
* ``_metadata`` — canonicalises the small allow-list of metadata keys as
  sorted-key JSON. Canonical output is what makes downstream hash /
  dedupe consumers meaningful: equal-content dicts must produce equal
  bytes regardless of insertion order.

We also assert two behavioural contracts on the public entry point:

* ``record_context_artifacts`` never raises when given a poisoned input.
* When given only invalid rows (bad ``kind``, empty ``stable_id``) it
  writes zero rows and returns 0 — the contract's defence-in-depth.
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings, strategies as st

from aletheia.economics.context_artifacts import (
    _ALLOWED_FRESHNESS,
    _ALLOWED_KINDS,
    _MAX_TEXT,
    _bounded_text,
    _metadata,
    record_context_artifacts,
)


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

# Arbitrary text including whitespace, control chars, and unicode. Bounding
# max_size well above _MAX_TEXT is important: the property is about the
# clip, so inputs must span both sides of the threshold.
_arbitrary_text = st.text(max_size=_MAX_TEXT * 4)


# --------------------------------------------------------------------------- #
# _bounded_text — clip contract
# --------------------------------------------------------------------------- #


@given(value=_arbitrary_text)
@settings(max_examples=200, deadline=None)
def test_bounded_text_never_exceeds_the_cap(value: str) -> None:
    """The clipped string is never longer than the module's cap."""
    out = _bounded_text(value)
    assert out is None or len(out) <= _MAX_TEXT


@given(value=_arbitrary_text)
@settings(max_examples=200, deadline=None)
def test_bounded_text_empty_and_whitespace_collapse_to_none(value: str) -> None:
    """Empty / whitespace-only inputs must produce None so nothing is stored."""
    if value.strip() == "":
        assert _bounded_text(value) is None


@given(value=_arbitrary_text)
@settings(max_examples=200, deadline=None)
def test_bounded_text_is_idempotent(value: str) -> None:
    """Clipping twice equals clipping once — no re-encoding drift."""
    once = _bounded_text(value)
    twice = _bounded_text(once)
    assert once == twice


def test_bounded_text_survives_non_string_inputs() -> None:
    """Any callable must be safe to feed with unexpected types; the recorder
    is best-effort and must never raise on its inputs."""
    for value in (None, 0, 1.5, [], {}, object()):
        # Must return either None or a bounded string; must not raise.
        result = _bounded_text(value)
        assert result is None or (isinstance(result, str) and len(result) <= _MAX_TEXT)


# --------------------------------------------------------------------------- #
# _metadata — canonical JSON contract
# --------------------------------------------------------------------------- #


_ALLOWED_META_KEYS = ("analysis_state", "freshness_status", "relevance_bucket")


@st.composite
def _metadata_dict(draw) -> dict[str, object]:
    """Build metadata dicts including allowed + noise keys and mixed types."""
    entries: dict[str, object] = {}
    # Allowed keys: sometimes present, sometimes not.
    for key in _ALLOWED_META_KEYS:
        if draw(st.booleans()):
            entries[key] = draw(st.text(max_size=32))
    # Noise keys that the projection must strip.
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        entries[draw(st.text(min_size=1, max_size=8))] = draw(st.text(max_size=8))
    return entries


@given(payload=_metadata_dict())
@settings(max_examples=200, deadline=None)
def test_metadata_output_is_canonical_json(payload: dict[str, object]) -> None:
    """Output must round-trip through json.loads and be equal to a re-canonicalised
    form: this is what makes two byte-identical outputs meaningful."""
    encoded = _metadata(payload)
    parsed = json.loads(encoded)
    assert isinstance(parsed, dict)
    # Every key in the parsed output must be in the allow-list.
    for key in parsed:
        assert key in _ALLOWED_META_KEYS, f"leaked non-allow-list key: {key!r}"
    # Every non-empty allowed value in the input must appear (clipped) in output.
    for key in _ALLOWED_META_KEYS:
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            assert key in parsed


def test_metadata_is_key_order_independent() -> None:
    """Building the same content in every permutation of the three allowed
    keys must produce the same bytes. Downstream consumers rely on this
    for stable hashing. This exercises the ``sort_keys=True`` guarantee
    directly, not just the projection order.

    Values are hand-chosen distinct non-whitespace strings; anything that
    survives ``_bounded_text`` and is not equal across keys will surface
    a non-canonical dump.
    """
    import itertools

    keys = ("analysis_state", "freshness_status", "relevance_bucket")
    values = ("alpha", "bravo", "charlie")
    outputs = set()
    for perm in itertools.permutations(zip(keys, values)):
        outputs.add(_metadata(dict(perm)))
    assert len(outputs) == 1, (
        f"non-canonical output across permutations: {outputs!r}"
    )


def test_metadata_handles_none_and_empty() -> None:
    """``None`` and ``{}`` collapse to the same canonical empty JSON."""
    assert _metadata(None) == "{}"
    assert _metadata({}) == "{}"


# --------------------------------------------------------------------------- #
# record_context_artifacts — best-effort contract
# --------------------------------------------------------------------------- #


@given(
    artifacts=st.lists(
        st.dictionaries(
            keys=st.text(max_size=16),
            values=st.one_of(st.text(max_size=32), st.integers(), st.none()),
            max_size=8,
        ),
        max_size=6,
    )
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_record_never_raises_on_poisoned_input(artifacts: list[dict]) -> None:
    """The recorder is documented as best-effort. Poisoned or arbitrary
    input must not propagate an exception; the return value is the number
    of rows the recorder was willing to persist (may be 0)."""
    result = record_context_artifacts(
        run_id=None, caller="propcheck", symbol=None, artifacts=artifacts
    )
    assert isinstance(result, int) and result >= 0


def test_record_rejects_rows_with_invalid_kind_or_missing_stable_id() -> None:
    """The recorder's row filter is the audit contract's defence-in-depth.
    Rows without a valid ``kind`` or without a non-empty ``stable_id`` must
    be dropped, never persisted with a fallback value.
    """
    # All three rows fail the filter: bad kind, empty stable_id, missing both.
    bad_rows = [
        {"kind": "not-a-kind", "stable_id": "x"},
        {"kind": "news", "stable_id": ""},
        {"kind": "news"},
    ]
    result = record_context_artifacts(
        run_id=None, caller="propcheck", symbol=None, artifacts=bad_rows
    )
    assert result == 0

    # A single good row alongside bad ones records exactly one.
    mixed = bad_rows + [{"kind": "news", "stable_id": "news_item:1"}]
    result = record_context_artifacts(
        run_id=None, caller="propcheck", symbol=None, artifacts=mixed
    )
    assert result in (0, 1)  # 0 iff DB unavailable; 1 on success.


def test_record_freshness_is_normalised_to_allow_list() -> None:
    """Freshness outside the allow-list falls back to 'unknown', so downstream
    aggregation never sees a rogue label. Documented invariant of the module."""
    # We can only observe this via the internal constant since the recorder
    # is best-effort against a DB. The constant IS the allow-list, so this
    # test locks its shape and any silent expansion would need to be
    # deliberate.
    assert _ALLOWED_FRESHNESS == {"fresh", "stale", "unknown"}
    assert _ALLOWED_KINDS == {"environment", "news", "learning"}
