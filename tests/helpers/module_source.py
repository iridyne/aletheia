"""Source of a module *including* the modules it re-exports from.

Seam modules ([`ARCHITECTURE.md#module-seams`](../docs/design/architecture/ARCHITECTURE.md))
split a facade's source across sibling files that the facade re-exports with
``from <package>.<leaf> import *``. A spec that asserts "the source of
``<module>`` contains X" then silently stops checking the moment X moves into a
seam — it stays green while covering nothing, or turns red for a move that
changed no behaviour.

``module_source`` returns the facade's own source plus the source of every
module it imports from within its own directory subtree (one level, no
recursion), which is exactly the seam set. Specs assert against that union so
the check survives further splits without being weakened.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _resolve(module: str) -> Path | None:
    """Map a dotted module name to its file, or ``None`` when it is not a repo
    module (stdlib, third-party)."""
    parts = module.split(".")
    if not parts or not all(parts):
        return None
    relative = Path(*parts)
    for candidate in (ROOT / "core" / relative, ROOT / relative):
        for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if path.is_file():
                return path
    return None


def seam_modules(relative_path: str | Path) -> list[Path]:
    """Modules ``relative_path`` re-exports from: wildcard imports, plus
    same-directory modules named ``<stem>_*`` (their explicit re-imports).

    Ordinary named imports of neighbours (``models``, ``sqlite``) are not seams
    and stay out, so the union is not loosened into "the whole directory".
    """
    path = ROOT / relative_path
    subtree = path.parent
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[Path] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        leaf = node.module.rsplit(".", 1)[-1]
        wildcard = any(alias.name == "*" for alias in node.names)
        if not wildcard and not leaf.startswith(f"{path.stem}_"):
            continue
        resolved = _resolve(node.module)
        if resolved is None or resolved == path:
            continue
        if subtree in resolved.parents:
            found.add(resolved)
    return sorted(found)


def module_source(relative_path: str | Path) -> str:
    """Facade source followed by the source of each module it re-exports from."""
    path = ROOT / relative_path
    parts = [path.read_text(encoding="utf-8")]
    parts.extend(
        seam.read_text(encoding="utf-8") for seam in seam_modules(relative_path)
    )
    return "\n".join(parts)
