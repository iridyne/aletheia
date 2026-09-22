"""Finite Analytics composition proof; no Gateway, scheduler threads or external IO."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", [None, "extensions.analytics", "broken_dependency"])
def test_report_composition_and_absence(entrypoint, missing, tmp_path):
    script = f"entrypoint={entrypoint!r}\nmissing={missing!r}\n" + '''
import asyncio, importlib, socket, sys
from aletheia.config import loader, secrets
loader._load_dotenv = lambda: None
def forbidden(*args, **kwargs):
    raise AssertionError("network, credential access or producer execution at composition")
secrets.get_secret = forbidden
socket.getaddrinfo = forbidden
socket.socket.connect = forbidden
from aletheia.agent_runtime import scheduler
from aletheia.analytics import go_live_scorecard as scorecard
scheduler.configure_analytics_reports(synthesize_memo=forbidden, build_feedback=forbidden, build_calibration=forbidden)
scorecard.configure_calibration_readers(score_reader=forbidden, report_reader=forbidden)
class MissingReports:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.analytics" if missing == "broken_dependency" else missing
        if fullname == target_name:
            raise ModuleNotFoundError("test", name=missing)
sys.meta_path.insert(0, MissingReports())
try:
    if entrypoint == "cli":
        from aletheia.main import _configure_runtime
        _configure_runtime()
        _configure_runtime()
    else:
        app = importlib.import_module("aletheia.server.app").app
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing
else:
    assert missing != "broken_dependency"
    from aletheia.storage.migrations import run_migrations
    run_migrations()
    if missing:
        assert scheduler._synthesize_trading_memo is None
        assert scheduler._build_reasoning_feedback is None
        assert scheduler._build_calibration_report is None
        assert scorecard._check_calibration()["score"] == 0
        assert scorecard._check_calibration()["status"] == "fail"
        failures = []
        scheduler._record_job_failure = lambda *args: failures.append(args)
        assert scheduler._run_insight_synthesizer()["status"] == "unavailable"
        assert scheduler._run_confidence_calibration()["status"] == "unavailable"
        assert len(failures) == 2
        # Absent daily report is neither completed nor queued for retry.
        scheduler._DAILY_LEARNING_CATCHUP_JOBS = (("insight_synthesizer", forbidden),)
        catchup = scheduler._run_learning_startup_catchup()
        assert catchup == {"children": [{"job_id": "insight_synthesizer", "status": "unavailable"}], "remaining_due": [], "retry_scheduled": False}
        from extensions.learning.control import get_learning_job_health
        assert get_learning_job_health("insight_synthesizer").input_watermark is None
        # Canonical maintenance already commits before optional rebuild.
        from aletheia.maintenance.outcome_integrity import remediate_outcome_history
        result = remediate_outcome_history(dry_run=False)
        assert result["rebuild"] == {"calibration_report": "unavailable", "reasoning_feedback": "unavailable"}
        from aletheia.diagnostics.doctor import Report, _probe_content_memo_quality
        report = Report()
        _probe_content_memo_quality(report)
        assert report.checks[0].detail == "no trading_memo yet"
    else:
        assert scheduler._synthesize_trading_memo.__module__ == "extensions.analytics.insight_synthesizer"
        assert scheduler._build_reasoning_feedback.__module__ == "extensions.analytics.reasoning_feedback"
        assert scheduler._build_calibration_report.__module__ == "extensions.analytics.confidence_calibration"
        assert scorecard._compute_calibration_score.__module__ == "extensions.analytics.confidence_calibration"
        assert scheduler._build_calibration_report() is None
        assert scheduler._build_reasoning_feedback() is None
        assert asyncio.run(scheduler._synthesize_trading_memo()) is None
    if entrypoint == "cli":
        import contextlib, io, json
        from aletheia.cli.knowledge_cmd import remediate_outcomes
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            remediate_outcomes(apply=True)
        rebuilt = json.loads(output.getvalue())["rebuild"]
        expected = "unavailable" if missing else "insufficient"
        assert rebuilt == {"calibration_report": expected, "reasoning_feedback": expected}
    # Registration calls the real scheduler composition, never starts workers.
    from aletheia.config.schema import AletheiaConfig
    cfg = AletheiaConfig()
    cfg.runtime.scheduler_enabled = True
    cfg.runtime.paused = False
    class FakeScheduler:
        def __init__(self, *args, **kwargs): self.jobs = []
        def add_job(self, *args, **kwargs): self.jobs.append(kwargs["id"])
        def add_listener(self, *args, **kwargs): pass
        def start(self, *args, **kwargs): pass
        def pause_job(self, *args, **kwargs): pass
        def get_jobs(self): return []
    scheduler.BackgroundScheduler = FakeScheduler
    scheduler.load_config = lambda: cfg
    scheduler._universe_scanner_startup_recovery_done = True
    scheduler._universe_scanner_audit_mirror_done = True
    jobs = scheduler.init_scheduler().jobs
    for job in ("insight_synthesizer", "confidence_calibration"):
        assert (job in jobs) is (missing is None), jobs
    assert "outcome_settler" in jobs
    if entrypoint == "web":
        import httpx
        async def check_routes():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for path in ("confidence-calibration", "reasoning-feedback"):
                    response = await client.get("/api/analytics/" + path)
                    assert response.status_code == (404 if missing else 200), response.text
                response = await client.get("/api/analytics/trade-stats")
                assert response.status_code == 200, response.text
        asyncio.run(check_routes())
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_rebuild_preserves_per_report_failure_truth():
    from aletheia.maintenance.outcome_integrity import _rebuild_allowed_reports

    def broken():
        raise RuntimeError("report write failed")

    assert _rebuild_allowed_reports(lambda: {}, broken) == {
        "calibration_report": "generated", "reasoning_feedback": "failed",
    }
    assert _rebuild_allowed_reports(lambda: None, None) == {
        "calibration_report": "insufficient", "reasoning_feedback": "unavailable",
    }


def test_missing_calibration_blocks_otherwise_green_scorecard(monkeypatch):
    from aletheia.analytics import go_live_scorecard as scorecard

    monkeypatch.setattr(scorecard, "_compute_calibration_score", None)
    monkeypatch.setattr(scorecard, "_get_calibration_report", None)
    for name in (
        "trade_volume", "profit_factor", "win_rate", "max_drawdown",
        "exchange_connectivity", "risk_violations", "data_coverage", "model_health", "reconciliation",
    ):
        monkeypatch.setattr(scorecard, "_check_" + name,
                            lambda *args: scorecard._criterion("other", "other", 100, 0.1, "ok"))
    monkeypatch.setattr(scorecard, "_check_positive_net_pnl", lambda: {"status": "pass", "detail": "ok"})
    result = scorecard.compute_scorecard()
    assert result["composite_score"] == 90
    assert result["blockers"]
    # The existing Live route requires green AND no blockers, not score alone.
    assert result["traffic_light"] == "green"
    assert not (result["composite_score"] >= 70 and not result["blockers"])
