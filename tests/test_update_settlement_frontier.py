"""Real SQLite proof for the update-only delivery-frontier projection."""

from __future__ import annotations


def test_fresh_cursor_with_pending_receipt_is_importable_then_settled():
    from extensions.observation_lab.canonical import (
        append_delivery_receipt,
        import_delivery_receipts,
    )
    from extensions.observation_lab.db import get_lab_db
    from extensions.observation_lab.update_settlement import (
        _delivery_transport_state,
    )

    get_lab_db()
    append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="update-settlement-frontier",
        resolution_reason="adapter_unavailable",
    )

    pending = _delivery_transport_state()

    assert pending["available"] is True
    assert pending["settled"] is False
    assert pending["reason"] == "delivery_import_pending"
    assert pending["outbox_pending_count"] == 1
    assert pending["lab_imported_watermark"] == 0

    imported = import_delivery_receipts(limit=1000)
    settled = _delivery_transport_state()

    assert imported["processed"] == 1
    assert imported["failed_sequence"] is None
    assert settled["available"] is True
    assert settled["settled"] is True
    assert settled["outbox_pending_count"] == 0
    assert settled["lab_imported_watermark"] == settled["outbox_high_watermark"]
