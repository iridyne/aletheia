"""One-shot codemod: replace silent except-pass blocks with logger.warning.

Run from the repository root:
    uv run python scripts/add_scheduler_warnings.py

Targets line numbers in core/aletheia/agent_runtime/scheduler.py. Each target
line must currently be a bare ``pass``; the script fails loudly otherwise, so
drifted line numbers cannot silently corrupt the file. Line 4194 is an
intentional silent pass and stays untouched.
"""
import ast
from pathlib import Path

PATH = Path("core/aletheia/agent_runtime/scheduler.py")

MESSAGES = {
    468: "scheduler: timeout worker close failed (loop state)",
    628: "scheduler: consecutive-failure audit write failed",
    740: "scheduler: job-state cleanup attribute already absent",
    1218: "scheduler: serenity startup catch-up audit write failed",
    1696: "scheduler: init cleanup shutdown failed after startup error",
    1798: "scheduler: resume-blocked audit write failed",
    1822: "scheduler: resume_job failed",
    4719: "scheduler: active-universe instrument lookup failed; using fallback",
}
KEEP = {4194}  # intentional silent pass


def main() -> None:
    lines = PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    for lineno, message in MESSAGES.items():
        line = lines[lineno - 1]
        indent = line[: len(line) - len(line.lstrip())]
        body = line.strip()
        assert body == "pass", f"line {lineno} is not a bare pass: {body!r}"
        lines[lineno - 1] = f"{indent}logger.warning({message!r})\n"
    for lineno in KEEP:
        assert lines[lineno - 1].strip() == "pass", f"keep line {lineno} changed"
    PATH.write_text("".join(lines), encoding="utf-8")
    ast.parse(PATH.read_text(encoding="utf-8"), filename=str(PATH))
    print("ok: 8 warnings inserted, 4194 untouched, AST parse clean")


if __name__ == "__main__":
    main()
