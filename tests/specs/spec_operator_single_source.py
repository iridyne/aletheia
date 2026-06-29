"""Spec: operator single-source-of-truth -- TS shell persists nothing locally (2026-06-28).

The authoritative record of operator state lives ONLY in Python SQLite tables; the TS
operator-shell only orchestrates + executes and must never write to local disk. Static
tripwire: no fs write calls / no fs import in operator-shell/src. Structural (L2a).
"""
from __future__ import annotations

import pathlib

from tests.specs.spec_meta import SpecClaim, spec_claim


def _root():
    return pathlib.Path(__file__).resolve().parents[2]


_DISK_WRITE = ("writeFileSync", "writeFile(", "appendFileSync", "appendFile(", "createWriteStream", "outputFile")
_FS_IMPORT = ("from 'fs'", 'from "fs"', "from 'node:fs'", 'from "node:fs"', "require('fs')", 'require("fs")')


@spec_claim(SpecClaim("PI-S06", "operator_layer", "single source of truth", "L2a",
    "TS operator-shell writes nothing to local disk: no fs write calls and no fs import"))
def test_ts_operator_shell_never_writes_disk():
    d = _root() / "operator-shell" / "src"
    assert d.is_dir(), "missing operator-shell/src at " + str(d)
    bad = []
    for ts in sorted(d.glob("*.ts")):
        code = ts.read_text(encoding="utf-8")
        for tok in _DISK_WRITE + _FS_IMPORT:
            if tok in code:
                bad.append(ts.name + ":" + tok)
    assert not bad, "TS operator-shell broke single-source-of-truth: " + ", ".join(bad)
