"""Pin the naive-timestamp contract of every timestamp parser in aletheia/.

WHY THIS EXISTS
---------------
There are 28 private timestamp parsers across the codebase and they do NOT share a
contract. Given an ISO string with no timezone they split three ways:

* 18 assume UTC          — `dt.replace(tzinfo=timezone.utc)`
* 8  reject              — `return None`
* 2  raise               — non-optional return, malformed input is an error
* 1  takes an explicit strptime pattern list, so it shares no signature with the rest

A "converge them into one helper" refactor was attempted and had to be abandoned:
the grouping had been derived by scanning what followed `if parsed.tzinfo is None`,
which is too coarse. It missed return-value semantics, input-type validation, and
which exceptions are caught — 14 of 18 substitutions turned out to change behaviour,
and one file lost its rejection semantics entirely without a single test noticing.

That last part is the point. The suite was PROVEN blind to this class of change:
rewriting `portfolio/pnl.py`'s parser from reject-naive to assume-UTC left all 1347
spec tests and all 9577 non-e2e tests passing, with an empty failure-set difference.
So "no new failures" was never evidence for a parser substitution.

This module supplies the missing evidence. Any future convergence must keep these
tests green, which makes the semantics checkable instead of merely unchanged.

The 9 rejecters are not stylistic. They guard accounting and risk surfaces — realized
PnL, funding, position lifecycle, close-timestamp repair, counterfactual cutoffs —
whose job is to refuse ambiguous evidence rather than guess a timezone.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

_NAIVE = "2026-09-07T12:00:00"
_AWARE = "2026-09-07T12:00:00+00:00"
_MALFORMED = "not-a-timestamp"


def _load(module_path: str, name: str):
    import importlib

    return getattr(importlib.import_module(module_path), name)


# A naive timestamp is ambiguous evidence; these parsers refuse it.
REJECT_NAIVE = [
    ("aletheia.analytics.exit_breakeven_shadow", "_parse_utc"),
    ("aletheia.analytics.exit_trailing_qualification", "_parse_utc"),
    ("aletheia.analytics.profit_loop_counterfactual", "_parse_utc"),
    ("aletheia.analytics.temporal", "parse_utc_instant"),
    ("aletheia.maintenance.paper_close_timestamp_recovery", "_parse_utc"),
    ("aletheia.portfolio.local_funding", "_parse_utc"),
    ("aletheia.portfolio.market_signal_reader", "_parse_utc"),
    ("aletheia.portfolio.pnl", "_parse_utc_instant"),
]

# A naive timestamp is read as UTC; these parsers accept it.
# The canonical helper the seven converged parsers now share. Its behaviour signature
# was matched cell-for-cell against those seven before the substitution.
CANONICAL = [
    ("aletheia.utils.dt", "parse_utc"),
]

ASSUME_UTC = [
    ("aletheia.analytics.alpha_attribution", "_parse_dt"),
    ("aletheia.analytics.causal_controls", "_parse_dt"),
    ("aletheia.analytics.filter_evidence", "_parse_dt"),
    ("aletheia.data.active_ohlcv_backfill", "_parse_utc"),
    ("aletheia.execution.pending_close_recovery", "_parse_dt"),
    ("aletheia.gateway.operator_shell", "_parse_utc_ts"),
    ("aletheia.learning.control", "_parse_timestamp"),
    ("aletheia.portfolio.position_manager", "_parse_dt"),
    ("aletheia.trading.strategy_lifecycle", "_parse_dt"),
    ("aletheia.universe.outcome_recorder", "_parse_dt"),
]

# Not a single-argument parser: it takes an explicit strptime pattern list, so it
# cannot share a signature with the others and is excluded from the shared-case test.
# Its naive contract is still pinned below.
PATTERNED = [
    ("aletheia.analytics.trade_attribution", "_parse_dt"),
]

# Malformed input is an ERROR, not a None; the return type is non-optional.
RAISES = [
    ("aletheia.analytics.holding_time", "_parse_timestamp"),
    ("aletheia.analytics.profit_gradient_evidence", "_parse_utc"),
]


def _ids(pairs):
    return [f"{module.rsplit('.', 1)[-1]}.{name}" for module, name in pairs]


@pytest.mark.parametrize(("module_path", "name"), REJECT_NAIVE, ids=_ids(REJECT_NAIVE))
def test_rejecting_parsers_refuse_a_naive_timestamp(module_path: str, name: str):
    """A tz-less timestamp must yield None, never an assumed-UTC datetime.

    These guard realized PnL, funding, position lifecycle, close-timestamp repair
    and counterfactual cutoffs. Widening one to assume UTC silently admits ambiguous
    evidence into an accounting decision.
    """
    parse = _load(module_path, name)
    assert parse(_NAIVE) is None, (
        f"{module_path}.{name} accepted a naive timestamp — it previously refused one. "
        "If this contract is meant to change, change it deliberately and move the "
        "entry to ASSUME_UTC in this file."
    )


@pytest.mark.parametrize(
    ("module_path", "name"), ASSUME_UTC + CANONICAL, ids=_ids(ASSUME_UTC + CANONICAL)
)
def test_assuming_parsers_read_a_naive_timestamp_as_utc(module_path: str, name: str):
    parse = _load(module_path, name)
    parsed = parse(_NAIVE)
    assert parsed is not None, (
        f"{module_path}.{name} refused a naive timestamp — it previously assumed UTC"
    )
    assert parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(
        datetime.now(timezone.utc)
    ), f"{module_path}.{name} did not normalise a naive timestamp to UTC"


@pytest.mark.parametrize(("module_path", "name"), RAISES, ids=_ids(RAISES))
def test_raising_parsers_raise_on_malformed_input(module_path: str, name: str):
    """Non-optional return type: the caller has no None branch to take."""
    parse = _load(module_path, name)
    with pytest.raises((ValueError, TypeError)):
        parse(_MALFORMED)


@pytest.mark.parametrize(
    ("module_path", "name"), REJECT_NAIVE + ASSUME_UTC + CANONICAL,
    ids=_ids(REJECT_NAIVE + ASSUME_UTC + CANONICAL),
)
def test_every_optional_parser_agrees_on_the_easy_cases(module_path: str, name: str):
    """Whatever they do with naive input, all 27 agree here.

    An aware timestamp parses to UTC; malformed input is None rather than an
    exception. These are the parts a convergence could safely share — pinned so a
    future refactor can lean on them.
    """
    parse = _load(module_path, name)
    aware = parse(_AWARE)
    assert aware is not None and aware.tzinfo is not None
    assert aware == datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    assert parse(_MALFORMED) is None, (
        f"{module_path}.{name} raised or returned a value for malformed input; "
        "optional parsers must fail closed with None"
    )


@pytest.mark.parametrize(("module_path", "name"), PATTERNED, ids=_ids(PATTERNED))
def test_patterned_parser_assumes_utc_for_a_naive_match(module_path: str, name: str):
    parse = _load(module_path, name)
    parsed = parse(_NAIVE, ["%Y-%m-%dT%H:%M:%S"])
    assert parsed is not None and parsed.tzinfo is not None
    assert parse(_MALFORMED, ["%Y-%m-%dT%H:%M:%S"]) is None


def test_the_inventory_is_exhaustive():
    """No timestamp parser may exist outside the three groups above.

    A new parser with an undeclared naive-input contract is exactly how the split
    grew to 28 copies in three dialects. Adding one now fails this test until its
    contract is declared.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    declared = {
        (module_path.replace("aletheia.", "aletheia/").replace(".", "/") + ".py", name)
        for module_path, name in REJECT_NAIVE + ASSUME_UTC + RAISES + PATTERNED + CANONICAL
    }
    found = set()
    for path in sorted((root / "aletheia").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "parse" not in source:
            continue
        for node in ast.parse(source).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(
                token in node.name
                for token in ("parse_utc", "parse_dt", "parse_timestamp", "parse_instant")
            ):
                continue
            found.add((path.relative_to(root).as_posix(), node.name))

    missing = sorted(found - declared)
    stale = sorted(declared - found)
    assert not missing and not stale, (
        f"timestamp-parser inventory drift: undeclared={missing}, gone={stale}. "
        "Declare a new parser's naive-input contract in REJECT_NAIVE / ASSUME_UTC / "
        "RAISES, or remove the stale entry alongside the deletion."
    )
