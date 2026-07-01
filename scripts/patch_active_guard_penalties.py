"""One-shot patch: add score_penalty/enforcement/block_candidate to existing active guards.

All 8 guards were auto-generated with no penalty fields (断口G). Run once:
  uv run python scripts/patch_active_guard_penalties.py
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

from aletheia.config.loader import get_data_dir
from aletheia.knowledge.frontmatter import parse_frontmatter, render_frontmatter

GUARDS_DIR = get_data_dir() / "knowledge" / "guards"

patched = 0
skipped = 0
errors = 0

for path in sorted(GUARDS_DIR.glob("*.md")):
    try:
        meta, body = parse_frontmatter(path)
        if not meta:
            skipped += 1
            continue

        changed = False
        if not meta.get("score_penalty"):
            meta["score_penalty"] = 10.0
            changed = True
        if not meta.get("enforcement"):
            meta["enforcement"] = "blocking"
            changed = True
        if not meta.get("block_candidate"):
            meta["block_candidate"] = True
            changed = True

        if changed:
            content = render_frontmatter(meta, body)
            path.write_text(content, encoding="utf-8")
            patched += 1
            print(f"  patched: {path.name} (guard_id={meta.get('guard_id', '?')})")
        else:
            skipped += 1

    except Exception as e:
        print(f"  ERROR: {path.name}: {e}", file=sys.stderr)
        errors += 1

print(f"\nDone: patched={patched} skipped={skipped} errors={errors}")
