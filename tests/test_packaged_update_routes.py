from __future__ import annotations

from starlette.requests import Request

from aletheia.server.routes import update as update_routes


def _request(*, referer: str = "", sec_fetch_site: str = "") -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if referer:
        headers.append((b"referer", referer.encode()))
    if sec_fetch_site:
        headers.append((b"sec-fetch-site", sec_fetch_site.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/update/apply",
            "raw_path": b"/api/update/apply",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8787),
        }
    )


def test_same_origin_confirmation_requires_browser_controlled_headers():
    assert update_routes._same_origin_dashboard_confirmation(
        _request(
            referer="http://127.0.0.1:8787/settings?tab=runtime",
            sec_fetch_site="same-origin",
        )
    )
    assert not update_routes._same_origin_dashboard_confirmation(
        _request(referer="http://evil.example/", sec_fetch_site="cross-site")
    )
    assert not update_routes._same_origin_dashboard_confirmation(
        _request(referer="http://127.0.0.1:8787/settings")
    )


def test_empty_script_request_still_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(update_routes, "is_preview_mode", lambda: False)
    monkeypatch.setattr(
        update_routes.applier,
        "apply_update",
        lambda: (_ for _ in ()).throw(AssertionError("applier must not run")),
    )

    result = update_routes.update_apply(
        _request(), update_routes.ApplyBody(confirm=False)
    )
    assert result["accepted"] is False
    assert result["reason"] == "confirmation_required"


def test_same_origin_dashboard_request_can_start_packaged_update(monkeypatch):
    monkeypatch.setattr(update_routes, "is_preview_mode", lambda: False)
    monkeypatch.setattr(update_routes, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(update_routes, "_is_packaged_update_mode", lambda: True)
    monkeypatch.setattr(
        update_routes.applier,
        "apply_update",
        lambda: {
            "mode": "packaged",
            "accepted": True,
            "applied": False,
            "operation_id": "update-test",
            "message": "started",
        },
    )

    result = update_routes.update_apply(
        _request(
            referer="http://localhost:8787/settings?tab=runtime",
            sec_fetch_site="same-origin",
        ),
        update_routes.ApplyBody(confirm=False),
    )
    assert result["accepted"] is True
    assert result["reason"] == "update_started_in_background"


def test_source_dashboard_apply_refuses_mutation_and_requires_cli(monkeypatch):
    monkeypatch.setattr(update_routes, "is_preview_mode", lambda: False)
    monkeypatch.setattr(update_routes, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(update_routes, "_is_packaged_update_mode", lambda: False)
    monkeypatch.setattr(
        update_routes.applier,
        "apply_update",
        lambda: (_ for _ in ()).throw(
            AssertionError("source API must never mutate the checkout")
        ),
    )

    result = update_routes.update_apply(
        _request(),
        update_routes.ApplyBody(confirm=True),
    )

    assert result == {
        "applied": False,
        "accepted": False,
        "mode": "development",
        "reason": "source_update_requires_cli",
        "message": (
            "Source-checkout updates must run through `aletheia update` so the "
            "external updater process can restart Main and verify Observer continuity."
        ),
    }


def test_rollback_requires_confirmation_before_calling_applier(monkeypatch):
    monkeypatch.setattr(update_routes, "is_preview_mode", lambda: False)
    monkeypatch.setattr(
        update_routes.applier,
        "rollback_update",
        lambda: (_ for _ in ()).throw(AssertionError("rollback applier must not run")),
    )

    result = update_routes.update_rollback(
        _request(), update_routes.ApplyBody(confirm=False)
    )
    assert result == {
        "accepted": False,
        "reason": "confirmation_required",
        "message": "Set confirm=true to request rollback.",
    }
