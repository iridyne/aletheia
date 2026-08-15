"""Plan Position-path compaction without deleting hot evidence.

The first archive PR deliberately exposes only a dry-run boundary. A future
compaction PR can add an explicit atomic delete path after real shadow parity.
"""

from __future__ import annotations

import argparse
import json

from aletheia.storage import repositories as repo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--position-id")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = repo.archive_position_path_segments(
        args.position_id,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
        dry_run=True,
        limit=args.limit,
    )
    result = {"mode": "dry-run", "compaction_enabled": False, **result}
    print(
        json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":") if args.as_json else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
