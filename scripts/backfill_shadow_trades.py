"""One-time backfill: resolve the historical shadow_trades backlog.

The `content.shadow_trades` doctor probe found 7560/8039 shadow trades unresolved
>24h. Their parent positions were opened before the b9ad957 opportunity_id fix and
carry NULL opportunity_id, so the event-driven resolver
(resolve_shadow_trades_for_position) returned 0 every time. This script resolves
them directly by each shadow's own 24h forward horizon, reading the historical
close from market_ohlcv — NOT the current price (these are up to ~13 days old, so
"current price" would be a meaningless exit).

It reuses the exact same logic as the periodic shadow_resolver scheduler job
(aletheia.analytics.shadow_trades.resolve_stale_shadows) so the one-time backfill
and the forward catch-up can never diverge.

DATA op — run from the repo root so get_db() targets the production data/app.db,
NOT a worktree's empty data dir:

    uv run python scripts/backfill_shadow_trades.py --dry-run   # inspect first
    uv run python scripts/backfill_shadow_trades.py             # then write

Symbols that left the universe have no OHLCV to resolve against; those rows are
counted as skipped_no_ohlcv and left unresolved (an honest, bounded residue).
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_shadow_trades")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill unresolved shadow trades by historical horizon.")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute and report counts without writing")
    parser.add_argument("--min-age-hours", type=int, default=24,
                        help="only resolve shadows older than this (default 24)")
    parser.add_argument("--horizon-hours", type=int, default=24,
                        help="forward horizon for the counterfactual exit (default 24)")
    parser.add_argument("--limit", type=int, default=100000,
                        help="max rows to process in one run (default 100000)")
    args = parser.parse_args()

    from aletheia.storage.migrations import run_migrations
    from aletheia.analytics.shadow_trades import resolve_stale_shadows

    run_migrations()

    counts = resolve_stale_shadows(
        min_age_hours=args.min_age_hours,
        horizon_hours=args.horizon_hours,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    mode = "DRY-RUN (no writes)" if args.dry_run else "APPLIED"
    logger.info(
        "shadow backfill %s — scanned=%d resolved=%d skipped_no_ohlcv=%d skipped_bad_entry=%d",
        mode, counts["scanned"], counts["resolved"],
        counts["skipped_no_ohlcv"], counts["skipped_bad_entry"],
    )
    if counts["skipped_no_ohlcv"]:
        logger.info(
            "  (%d rows had no OHLCV at +%dh — likely delisted/out-of-universe symbols; "
            "left unresolved by design)",
            counts["skipped_no_ohlcv"], args.horizon_hours,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
