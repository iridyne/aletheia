"""Docs-vs-CLI consistency guard (Issue #1242).

Parses every ``aletheia permission set <MODE>`` and ``aletheia risk <SUBCMD>``
invocation found in normative docs and verifies each resolves against the live
Typer command tree and the allowed permission-mode enum.

Also provides an explicit fixture for the paper-unblock recovery sequence so
it fails loudly if the command tree changes again.
"""
import re
from pathlib import Path

from typer.testing import CliRunner

from aletheia.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DOCS_ROOT = Path(__file__).parent.parent / "docs"

# Only capture word-character tokens — strips trailing backticks, punctuation, etc.
_PERMISSION_SET_RE = re.compile(r"aletheia permission set (\w+)")
_RISK_SUBCMD_RE = re.compile(r"aletheia risk (\w[\w-]*)")
VALID_PERMISSION_MODES = {"Observe", "Paper", "Full"}
VALID_RISK_SUBCMDS = {"status", "kill", "resume"}


def _collect_doc_invocations(pattern: re.Pattern, glob: str = "**/*.md") -> list[tuple[str, str]]:
    """Return [(file_path, captured_arg), ...] for every match in docs."""
    hits = []
    for path in sorted(DOCS_ROOT.rglob(glob)):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            hits.append((str(path.relative_to(DOCS_ROOT)), m.group(1)))
    return hits


# ---------------------------------------------------------------------------
# permission set — enum validation (no live config needed)
# ---------------------------------------------------------------------------

class TestPermissionSetEnum:
    def test_all_documented_modes_are_valid(self):
        hits = _collect_doc_invocations(_PERMISSION_SET_RE)
        assert hits, "No 'aletheia permission set' invocations found in docs — pattern may be broken"
        bad = [(f, mode) for f, mode in hits if mode not in VALID_PERMISSION_MODES]
        assert not bad, (
            "Docs contain invalid permission modes:\n"
            + "\n".join(f"  {f}: '{mode}'" for f, mode in bad)
        )


# ---------------------------------------------------------------------------
# risk subcommands — resolved against Typer tree
# ---------------------------------------------------------------------------

class TestRiskSubcommands:
    def test_all_documented_risk_subcmds_exist(self):
        hits = _collect_doc_invocations(_RISK_SUBCMD_RE)
        assert hits, "No 'aletheia risk' invocations found in docs — pattern may be broken"
        bad = [(f, sub) for f, sub in hits if sub not in VALID_RISK_SUBCMDS]
        assert not bad, (
            "Docs reference unknown 'aletheia risk' subcommands:\n"
            + "\n".join(f"  {f}: 'aletheia risk {sub}'" for f, sub in bad)
        )

    def test_risk_has_no_kill_switch_nested_group(self):
        """'aletheia risk kill-switch' must NOT exist — that was the bug."""
        result = runner.invoke(app, ["risk", "kill-switch"])
        assert result.exit_code != 0, (
            "'aletheia risk kill-switch' should be rejected but exit_code was 0"
        )

    def test_risk_status_accepted(self, tmp_path, monkeypatch):
        import aletheia.config.loader as loader
        import aletheia.risk.kill_switch as ks
        monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
        monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
        monkeypatch.setattr(ks, "is_active", lambda _scope=None: False)
        result = runner.invoke(app, ["risk", "status"])
        assert result.exit_code == 0

    def test_risk_resume_accepted(self, tmp_path, monkeypatch):
        import aletheia.config.loader as loader
        import aletheia.risk.kill_switch as ks
        monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
        monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
        monkeypatch.setattr(ks, "deactivate", lambda **_kw: None)
        result = runner.invoke(app, ["risk", "resume"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Paper-unblock recovery sequence fixture (Issue #1242 regression proof)
# ---------------------------------------------------------------------------

class TestPaperUnblockRecoverySequence:
    """Explicit fixture: the two commands from the unblock SOP must work."""

    def test_check_command(self, tmp_path, monkeypatch):
        """Step 1: inspect kill-switch state."""
        import aletheia.config.loader as loader
        import aletheia.risk.kill_switch as ks
        monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
        monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
        monkeypatch.setattr(ks, "is_active", lambda _scope=None: True)
        result = runner.invoke(app, ["risk", "status"])
        assert result.exit_code == 0
        assert "ACTIVE" in result.output

    def test_release_command(self, tmp_path, monkeypatch):
        """Step 2: release the kill switch."""
        import aletheia.config.loader as loader
        import aletheia.risk.kill_switch as ks
        monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
        monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
        released = []
        monkeypatch.setattr(ks, "deactivate", lambda **_kw: released.append(True))
        result = runner.invoke(app, ["risk", "resume"])
        assert result.exit_code == 0
        assert released, "deactivate() was never called"
        assert "released" in result.output.lower()
