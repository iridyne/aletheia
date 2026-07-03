"""Spec: High-Dimensional Insight Feedback Loop (Round 2 negative-feedback closure).

Owner directive (2026-06-28): negative-feedback learning must be HIGH-DIMENSIONAL
(Agent reasons about WHY trades lost / fits error patterns — not low-level
"block this symbol/direction") and SELF-EVOLVING (later evidence can OVERTURN an
earlier judgment). The design is docs/plans/insight_feedback_loop.md +
docs/plans/round2_negative_feedback_loop_closure.md.

These claims guard the three defects found in the audit:
  ① regime dimension collapse (regime_tag never snapshotted at entry)   → IFL-S03 (A1)
  ② dirty Round-1 data ingested (no clean-window floor)                 → IFL-S01 / IFL-B01 (A2)
  ③ no refutable self-evolution (memo_effectiveness missing,            → IFL-S02 / IFL-B03 (B)
     direction-bias cap declared-but-unenforced)

GREEN claims are implemented. xfail(strict) claims are tripwires for work still
in progress — they flip to GREEN as each defect is closed and the xfail marker
is removed in the same commit. DO NOT delete a tripwire to make CI green.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (_project_root() / rel).read_text(encoding="utf-8")


_LEARNERS = [
    "aletheia/analytics/insight_synthesizer.py",
    "aletheia/analytics/confidence_calibration.py",
    "aletheia/analytics/reasoning_feedback.py",
]


# ---------------------------------------------------------------------------
# ② Clean-window floor (A2) — IMPLEMENTED
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("IFL-S01", "insight_feedback_loop", "§Defect② clean-window", "L2a",
              "every learning data-fetch (synthesizer/calibration/reasoning) routes its "
              "rolling cutoff through clean_window_cutoff so pre-floor dirty data is excluded"),
)
def test_all_learners_apply_clean_window_floor():
    """All three high-dim learners MUST filter their data window through the
    clean-window floor — otherwise Round-1 dirty data biases the conclusions."""
    for rel in _LEARNERS:
        src = _src(rel)
        assert "clean_window_cutoff" in src, (
            f"{rel} does not call clean_window_cutoff — it would ingest pre-2026-06-25 "
            "dirty Round-1 data and learn biased ('all contra') patterns"
        )


@spec_claim(
    SpecClaim("IFL-B01", "insight_feedback_loop", "§Defect② clean-window", "L2b",
              "clean_window_cutoff returns max(rolling, floor): never admits pre-floor data, "
              "becomes a no-op once the rolling window starts after the floor"),
)
def test_clean_window_cutoff_behavior():
    from aletheia.analytics.learning_window import (
        clean_window_cutoff,
        LEARNING_DATA_FLOOR_ISO,
    )

    # rolling cutoff BEFORE the floor -> clamped up to the floor (dirty data excluded)
    assert clean_window_cutoff("2026-05-01T00:00:00+00:00") == LEARNING_DATA_FLOOR_ISO
    # rolling cutoff AFTER the floor -> returned unchanged (floor is a no-op)
    after = "2026-08-01T00:00:00+00:00"
    assert clean_window_cutoff(after) == after
    # exactly at the floor -> floor
    assert clean_window_cutoff(LEARNING_DATA_FLOOR_ISO) == LEARNING_DATA_FLOOR_ISO
    # malformed -> fail safe to floor (never ingest pre-floor data)
    assert clean_window_cutoff("not-a-timestamp") == LEARNING_DATA_FLOOR_ISO


# ---------------------------------------------------------------------------
# ③ Refutable self-evolution (B) — TRIPWIRES (work in progress)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("IFL-S02", "insight_feedback_loop", "§Defect③ Module J", "L2a",
              "memo_effectiveness module exists and exposes a prior-memo evaluation entry "
              "point so a later result can overturn/downweight an earlier judgment"),
)
def test_memo_effectiveness_module_exists():
    mod = importlib.import_module("aletheia.analytics.memo_effectiveness")
    assert hasattr(mod, "evaluate_prior_memo"), (
        "memo_effectiveness must expose evaluate_prior_memo()"
    )


@spec_claim(
    SpecClaim("IFL-B04", "insight_feedback_loop", "§Defect③ Module J", "L2b",
              "evaluate_prior_memo OVERTURNS a prior judgment when subsequent results "
              "contradict it: a memo claiming 'long strong' followed by all-losing long "
              "trades yields an overturned rule + accuracy_score written back to the memo"),
)
def test_prior_memo_overturned_by_subsequent_results(tmp_path, monkeypatch):
    """The core self-evolution invariant: a later result overturns an earlier judgment."""
    import json
    import sqlite3
    from datetime import datetime, timedelta, timezone

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item, list_knowledge_items
    from aletheia.analytics.memo_effectiveness import evaluate_prior_memo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    now = datetime.now(timezone.utc)
    gen = (now - timedelta(hours=12)).isoformat()
    memo = {
        "generated_at": gen,
        "valid_until": (now + timedelta(hours=60)).isoformat(),
        "data_window_days": 30, "sample_size": 20,
        "regime_playbook": [
            {"regime": "unknown", "direction": "long", "edge": "strong",
             "win_rate": 70.0, "avg_pnl_pct": 1.0, "sample_size": 10},
        ],
        "timing_windows": [], "symbol_affinity": [], "anti_patterns": [],
        "direction_summary": {}, "key_insight": "",
    }
    insert_knowledge_item(KnowledgeItem(
        item_type="trading_memo", title="TradingMemo test",
        content=json.dumps(memo),
        tags=json.dumps({"valid_until": memo["valid_until"]}),
    ))

    # 4 LONG trades opened AFTER the memo, all losses → the "long strong" claim was wrong.
    opened = (now - timedelta(hours=6)).isoformat()
    for i in range(4):
        conn.execute(
            "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
            "execution_mode,status,opened_at,realized_pnl,regime_tag) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"pos{i}", "BTC/USDT", "perp", "long", 1.0, 100.0,
             "paper_internal", "closed", opened, -5.0, None),
        )
    conn.commit()

    verdict = evaluate_prior_memo()
    assert verdict is not None and verdict["n_trades"] >= 4
    overturned = verdict["overturned_rules"]
    assert any(r["direction"] == "long" and r["claimed_edge"] == "strong" for r in overturned), (
        f"a 'long strong' claim contradicted by 4 losing longs must be overturned; got {overturned}"
    )
    assert verdict["accuracy_score"] == 0.0

    # The verdict is written back onto the prior memo's tags — a durable trace the
    # next memo/prompt can learn from (refutable self-evolution).
    tags = json.loads(list_knowledge_items(item_type="trading_memo", limit=1)[0]["tags"])
    assert tags.get("accuracy_score") == 0.0
    assert tags.get("overturned_count") >= 1


@spec_claim(
    SpecClaim("IFL-B05", "insight_feedback_loop", "§Defect③ Module J", "L2b",
              "render_memo_for_observer surfaces prior_corrections, so an overturned "
              "judgment actually reaches the observer/PI prompt — the revision is visible "
              "to the agent, not just stored"),
)
def test_prior_corrections_rendered_into_prompt():
    from aletheia.analytics.insight_synthesizer import render_memo_for_observer

    memo = {
        "sample_size": 12, "data_window_days": 30, "direction_summary": {},
        "regime_playbook": [], "timing_windows": [], "symbol_affinity": [],
        "anti_patterns": [], "key_insight": "",
        "prior_corrections": [
            {"direction": "long", "regime": "bear", "claimed_edge": "strong",
             "actual_win_rate": 20.0, "sample": 5,
             "reason": "claimed strong edge, subsequent trades show weak"},
        ],
    }
    text = render_memo_for_observer(memo)
    assert "Corrections" in text or "overturned" in text.lower(), (
        "prior_corrections must be rendered into the prompt so the revision is visible"
    )
    assert "20" in text and "long" in text, "the overturned rule's specifics must surface"


@spec_claim(
    SpecClaim("IFL-B06", "insight_feedback_loop", "§Time decay", "L2b",
              "render_memo_for_observer applies memo_half_life_weight so stale "
              "regime_playbook guidance is visibly downweighted in the prompt"),
)
def test_memo_half_life_weight_rendered_into_prompt():
    from datetime import datetime, timedelta, timezone

    from aletheia.analytics.insight_synthesizer import render_memo_for_observer
    from aletheia.analytics.memo_effectiveness import memo_half_life_weight

    now = datetime.now(timezone.utc)
    generated_at = (now - timedelta(days=7)).isoformat()
    assert 0.49 <= memo_half_life_weight(generated_at, now=now) <= 0.51

    memo = {
        "generated_at": generated_at,
        "sample_size": 12, "data_window_days": 30,
        "direction_summary": {},
        "regime_playbook": [
            {"direction": "long", "regime": "bull", "win_rate": 80.0,
             "avg_pnl_pct": 1.2, "sample_size": 8, "edge": "strong"},
        ],
        "timing_windows": [], "symbol_affinity": [], "anti_patterns": [],
        "key_insight": "", "prior_corrections": [],
    }
    text = render_memo_for_observer(memo)
    assert "Memo time weight" in text
    assert "time-weighted 40.0%" in text


# ---------------------------------------------------------------------------
# ① regime dimension (A1) — IMPLEMENTED
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("IFL-S03", "insight_feedback_loop", "§Defect① regime dimension", "L2b",
              "the regime stamped on a new Position reads the real market regime "
              "(market_regimes.state), not macro risk_regime — so the high-dim memo's "
              "regime_playbook gets a true dimension instead of collapsing to 'unknown'"),
)
def test_position_regime_reads_market_regime(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timezone

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.execution import position_enrichment, paper_broker

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    # The regime detector recorded a real market regime ('bear').
    conn.execute(
        "INSERT INTO market_regimes (id, state, detected_at) VALUES (?, ?, ?)",
        ("mr1", "bear", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    # Both broker regime sources must stamp the MARKET regime, not 'neutral'/'unknown'.
    assert position_enrichment.current_regime() == "bear", (
        "current_regime must read market_regimes.state (A1) — got non-market value"
    )
    assert paper_broker._current_regime() == "bear", (
        "_current_regime must mirror the same market-regime source as enrichment"
    )


@spec_claim(
    SpecClaim("IFL-B03", "insight_feedback_loop", "§Defect③ bias cap", "L2b",
              "MAX_DIRECTION_BIAS_STRENGTH is APPLIED: an extremely one-sided sample "
              "(>0.75 one direction) yields bias_capped=True and a neutral bias, so the "
              "memo never reinforces the over-concentrated side (positive-feedback-trap guard)"),
)
def test_direction_bias_cap_is_enforced():
    from aletheia.analytics.insight_synthesizer import _compute_direction_summary

    # 9 short / 1 long = 90% one-sided, mirroring Round 1's short 17/23 concentration.
    # Shorts "win" and longs "lose" so the naive avg-PnL rule would scream short_preferred.
    rows = [{"direction": "short", "realized_pnl": 5.0, "entry_price": 100.0, "quantity": 1.0}
            for _ in range(9)]
    rows += [{"direction": "long", "realized_pnl": -5.0, "entry_price": 100.0, "quantity": 1.0}]

    summary = _compute_direction_summary(rows)
    assert summary.get("bias_capped") is True, (
        "90% one-sided sample must trip the bias cap (bias_capped=True)"
    )
    assert summary["bias"] == "balanced", (
        f"over-concentrated direction must NOT be reinforced; got bias={summary['bias']!r}. "
        "A one-sided sample should yield neutral bias, not short_preferred."
    )

    # Control: a balanced sample is NOT capped and can express a real edge.
    balanced = [{"direction": "long", "realized_pnl": 5.0, "entry_price": 100.0, "quantity": 1.0}
                for _ in range(5)]
    balanced += [{"direction": "short", "realized_pnl": -5.0, "entry_price": 100.0, "quantity": 1.0}
                 for _ in range(5)]
    bsum = _compute_direction_summary(balanced)
    assert bsum.get("bias_capped") is False
    assert bsum["bias"] == "long_preferred"


# ---------------------------------------------------------------------------
# PCI-G02 — Learning write-path DB-only invariant (2026-06-30)
# Contract: PI_OPERATOR_DESIGN_DECISIONS §宪法专有 / knowledge_contract §8
# The learning mechanism may ONLY write to the database (trading_memo / lesson
# knowledge items). It must never open() a prompt file on the filesystem.
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PCI-G02", "insight_feedback_loop",
        "§pi_constitution learning_db_only_writes",
        "L2a",
        "Learning modules (aletheia/learning/, aletheia/analytics/) do not call "
        "open(..., 'w') or pathlib.Path.write_text/write_bytes on any path under "
        "agent_shell/prompts/ or containing SYSTEM_PROMPT — learning can only "
        "append trading_memo/lesson records to the database, never overwrite "
        "prompt files. Contract: knowledge_contract §8 (2026-06-30).",
        status="implemented",
    ),
)
def test_learning_path_does_not_write_prompt_files():
    """AST-scan every learning/analytics module for file-write calls targeting
    prompt paths. The invariant: knowledge-write path = DB only.

    We look for:
      - open(path, 'w' / 'wb' / 'a') where path contains 'prompt' or 'SYSTEM_PROMPT'
      - Path(...).write_text / .write_bytes on prompt-ish arguments

    Because call-argument values are partially dynamic, we scan for the *existence*
    of write-open with any literal containing 'prompt' as a conservative check.
    """
    import ast as _ast

    _PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
    _SCAN_DIRS = [
        _PROJECT_ROOT / "aletheia" / "learning",
        _PROJECT_ROOT / "aletheia" / "analytics",
    ]

    violations: list[str] = []

    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            tree = _ast.parse(src, filename=str(py_file))

            for node in _ast.walk(tree):
                # Pattern 1: open("...prompt...", "w") or open(..., mode="w")
                if isinstance(node, _ast.Call):
                    func = node.func
                    is_open_call = (
                        (isinstance(func, _ast.Name) and func.id == "open")
                        or (isinstance(func, _ast.Attribute) and func.attr == "open")
                    )
                    if is_open_call:
                        # Check positional args for write mode
                        args = node.args
                        kws = {k.arg: k.value for k in node.keywords}
                        mode_node = args[1] if len(args) > 1 else kws.get("mode")
                        is_write_mode = (
                            isinstance(mode_node, _ast.Constant)
                            and isinstance(mode_node.value, str)
                            and any(m in mode_node.value for m in ("w", "a"))
                        )
                        if is_write_mode:
                            # Check if any string arg mentions 'prompt'
                            path_node = args[0] if args else kws.get("file")
                            if (isinstance(path_node, _ast.Constant)
                                    and isinstance(path_node.value, str)
                                    and "prompt" in path_node.value.lower()):
                                violations.append(
                                    f"{py_file.name}:line {node.lineno}: "
                                    f"open({path_node.value!r}, write) — "
                                    "writing prompt file from learning module"
                                )

                # Pattern 2: .write_text / .write_bytes method calls on Path
                if isinstance(node, _ast.Call):
                    func = node.func
                    if (isinstance(func, _ast.Attribute)
                            and func.attr in ("write_text", "write_bytes")):
                        # Inspect the object being written to for 'prompt'
                        obj = func.value
                        if (isinstance(obj, _ast.Call)
                                and isinstance(obj.func, _ast.Name)
                                and obj.func.id == "Path"):
                            path_args = obj.args
                            if path_args and isinstance(path_args[0], _ast.Constant):
                                val = str(path_args[0].value)
                                if "prompt" in val.lower():
                                    violations.append(
                                        f"{py_file.name}:line {node.lineno}: "
                                        f"Path({val!r}).{func.attr}() — "
                                        "writing prompt file from learning module"
                                    )

    assert not violations, (
        "PCI-G02 VIOLATED: learning module(s) write to prompt file paths. "
        "Learning must only write to the database (insert_knowledge_item), "
        f"never to filesystem prompt files: {violations}"
    )
