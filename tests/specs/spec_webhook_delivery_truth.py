"""Executable Settings contract for sanitized Webhook delivery truth."""

import json

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "SETTINGS-WEBHOOK-S01",
        "settings_control_contract",
        "§Contract Webhook delivery truth",
        "L2b",
        "Settings exposes only the newest validated, sanitized Webhook attempt.",
    )
)
def test_webhook_delivery_read_model_is_sanitized(monkeypatch):
    from aletheia import notifications
    from aletheia.storage import repositories

    monkeypatch.setattr(
        repositories,
        "reconcile_audit_event_outbox",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        repositories,
        "latest_mirrored_webhook_delivery_event",
        lambda: {
            "after_state": json.dumps({
                "status": "failed",
                "attempted_at": "2026-09-02T08:00:00+00:00",
                "receiver_host": "receiver.example",
                "event_type": "alert",
                "entity_type": "gateway",
                "entity_id": "gateway",
                "http_status": 500,
                "error_class": "HTTPError",
                "latency_ms": 12,
                "url": "https://receiver.example/path-secret?query-secret",
                "response_body": "body-secret",
            }),
        },
    )

    status = notifications.webhook_delivery_status()

    assert status["status"] == "failed"
    serialized = json.dumps(status)
    assert "path-secret" not in serialized
    assert "query-secret" not in serialized
    assert "body-secret" not in serialized


def test_webhook_delivery_read_model_fails_closed_when_audit_is_unavailable(
    monkeypatch,
):
    import sqlite3

    from aletheia import notifications
    from aletheia.storage import repositories

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("audit unavailable")

    monkeypatch.setattr(repositories, "reconcile_audit_event_outbox", unavailable)

    assert notifications.webhook_delivery_status() is None
