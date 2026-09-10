"""Read-only legacy strategy status used by the existing market observers."""

def _truthy_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none", "null"}
    return bool(value)


def is_paper_trading_paused(record: dict | None) -> bool:
    return _truthy_flag((record or {}).get("paper_trading_paused"))


def is_active_paper_strategy(record: dict | None) -> bool:
    if not record:
        return False
    return record.get("state") in {"paper_candidate", "paper_running"} and not is_paper_trading_paused(record)
