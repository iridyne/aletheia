"""Spec: Operator Identity Contract (#1143)

Executable claims that the three canonical identities — System Operator,
Apex Loop (internal governance loop), and Trading Decision Specialist
(pi_daemon) — are correctly represented across active code.

Invariants enforced here:
  ID-S01  No "PI Operator" or "PI Agent" in active user-facing strings.
  ID-S02  Apex Loop is not a separate scheduler job or agent; it runs only
          as the System Operator governance loop (pi_operator_loop).
  ID-S03  Trading Decision Specialist (pi_daemon) exists as a bounded
          sub-authority of System Operator — not as System Operator itself.
  ID-S04  The pi_operator_loop scheduler job is registered; governance loop
          is wired to the operator_loop module.

Contract authority: #1080, #1143
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parent.parent.parent

_ACTIVE_FILES = [
    _ROOT / "aletheia" / "agent_shell" / "operator_loop.py",
    _ROOT / "aletheia" / "agent_runtime" / "scheduler.py",
    _ROOT / "aletheia" / "server" / "routes" / "live.py",
    _ROOT / "aletheia" / "universe" / "allocator.py",
    _ROOT / "aletheia" / "universe" / "signal_emitter.py",
    _ROOT / "aletheia" / "runtime_continuity" / "__init__.py",
    _ROOT / "aletheia" / "diagnostics" / "doctor.py",
    _ROOT / "operator-shell" / "src" / "server.ts",
    _ROOT / "operator-shell" / "src" / "operator_loop.ts",
]

# Stable internal identifiers that are intentionally kept (trust boundary).
_ALLOWED_LEGACY_IDS = frozenset({
    "pi_operator_loop",
    "pi_daemon",
    "pi_trading_decision",
    "PiDaemonConfig",
    "pi_daemon_config",
    "notify_pi_daemon",
})


def _is_allowed_line(line: str) -> bool:
    """Return True if 'PI' appears only in whitelisted internal identifiers."""
    import re
    # Strip known-safe identifiers, then check if "PI " or "PI\n" remains.
    stripped = line
    for safe in _ALLOWED_LEGACY_IDS:
        stripped = stripped.replace(safe, "")
    # "PI Agent" or "PI Operator" as a human-readable phrase is the violation.
    return not re.search(r"\bPI (Agent|Operator|operator|agent)\b", stripped)


@spec_claim(
    SpecClaim(
        "ID-S01", "operator_identity", "§identity no-PI-string in active code", "L2b",
        'Active source files must not use "PI Agent" or "PI Operator" as human-readable strings',
    ),
)
def test_no_pi_agent_or_operator_strings():
    """Scan active source files for disallowed 'PI Agent' / 'PI Operator' strings."""
    violations: list[str] = []
    for path in _ACTIVE_FILES:
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _is_allowed_line(line):
                violations.append(f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}")
    assert not violations, (
        "Disallowed 'PI Agent' / 'PI Operator' found in active source:\n"
        + "\n".join(violations)
    )


@spec_claim(
    SpecClaim(
        "ID-S02", "operator_identity", "§apex-loop is not a separate scheduler job", "L2a",
        "Apex Loop is internal to System Operator — no separate 'apex_loop' scheduler job",
    ),
)
def test_apex_loop_not_separate_scheduler_job():
    """scheduler.py must not register an 'apex_loop' or 'apex_supervision' job."""
    src = (_ROOT / "aletheia" / "agent_runtime" / "scheduler.py").read_text(encoding="utf-8")
    assert "apex_loop" not in src, (
        "scheduler.py registers an 'apex_loop' job — Apex Loop must remain internal "
        "to the System Operator governance loop (pi_operator_loop), not a separate scheduler job"
    )
    assert "apex_supervision" not in src, (
        "scheduler.py registers an 'apex_supervision' job — must be internal to operator loop"
    )


@spec_claim(
    SpecClaim(
        "ID-S03", "operator_identity", "§trading-decision is bounded specialist, not System Operator", "L2a",
        "pi_daemon.py must not import or invoke operator_loop — it is a bounded Trading Decision specialist",
    ),
)
def test_trading_decision_not_system_operator():
    """pi_daemon.py must not call run_operator_loop — Trading Decision does not govern."""
    pi_daemon = _ROOT / "aletheia" / "agent_runtime" / "pi_daemon.py"
    assert pi_daemon.exists(), "pi_daemon.py not found"
    src = pi_daemon.read_text(encoding="utf-8")
    assert "run_operator_loop" not in src, (
        "pi_daemon.py calls run_operator_loop — Trading Decision specialist must not "
        "invoke System Operator governance loop; those are separate authorities"
    )
    assert "operator_loop" not in src, (
        "pi_daemon.py imports operator_loop — Trading Decision must not depend on "
        "System Operator governance module"
    )


@spec_claim(
    SpecClaim(
        "ID-S04", "operator_identity", "§pi_operator_loop job is registered in scheduler", "L2b",
        "scheduler.py must register pi_operator_loop as the System Operator governance job",
    ),
)
def test_operator_governance_loop_registered():
    """scheduler.py must register pi_operator_loop and wire it to operator_loop module."""
    src = (_ROOT / "aletheia" / "agent_runtime" / "scheduler.py").read_text(encoding="utf-8")
    assert 'id="pi_operator_loop"' in src, (
        "pi_operator_loop job not found in scheduler.py — "
        "System Operator governance loop must be registered"
    )
    assert "operator_loop" in src, (
        "operator_loop not referenced in scheduler.py — "
        "governance loop must delegate to aletheia.agent_shell.operator_loop"
    )
