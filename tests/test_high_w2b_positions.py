"""Tests for #1467 and #1468 — positions safety issues.

#1467: Trailing stop DB commit happens before exchange sync
#1468: One position exception aborts all position evaluation
"""

import ast


def test_1467_trailing_stop_exchange_before_db():
    """#1467: exchange sync MUST happen before local DB commit in trailing stop.

    If the exchange call fails we must NOT update the local DB (no split state).

    The persist step is matched by the names it can legitimately carry rather than
    one literal: this test asserted `persist_position_exit_plan`, which was later
    renamed to `save_exit_plan`, so it started failing while the invariant it
    guards was still intact. A rename must not read as a safety regression, and a
    genuinely removed persist step must still fail.
    """
    with open("core/aletheia/portfolio/position_manager.py") as f:
        source = f.read()
    tree = ast.parse(source)
    
    # Find _maybe_update_trailing_stop function
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_maybe_update_trailing_stop":
            func = node
            break
    
    assert func is not None, "_maybe_update_trailing_stop not found"
    
    # Extract function body as text
    lines = source.split("\n")
    func_start = func.lineno - 1
    func_end = func.end_lineno
    func_text = "\n".join(lines[func_start:func_end])
    
    # Find positions of key calls
    exchange_call_pos = func_text.find("replace_exchange_protective_stop")
    persist_names = ("save_exit_plan", "persist_position_exit_plan", "update_position")
    persist_positions = [
        found for name in persist_names if (found := func_text.find(name)) != -1
    ]
    persist_call_pos = min(persist_positions) if persist_positions else -1

    assert exchange_call_pos != -1, "replace_exchange_protective_stop call not found"
    assert persist_call_pos != -1, (
        f"no local persist call found; expected one of {persist_names}"
    )
    
    # Exchange call MUST come before persist call (lower position = earlier in text)
    assert exchange_call_pos < persist_call_pos, (
        f"Exchange sync ({exchange_call_pos}) must come before DB persist ({persist_call_pos})"
    )
    
    # Verify the fix comment is present
    assert "#1467" in func_text, "Fix comment #1467 not found"


def test_1468_position_evaluation_has_exception_guard():
    """#1468: each position evaluation must be wrapped in try/except.
    
    One position raising an exception must not abort the entire loop.
    Source inspection: the for loop body should have a broad try/except
    around the core evaluation logic.
    """
    with open("core/aletheia/portfolio/position_manager.py") as f:
        source = f.read()
    
    lines = source.split("\n")
    
    # Find the main position loop in evaluate_open_positions
    loop_line = None
    for i, line in enumerate(lines):
        if "for p in all_open:" in line:
            loop_line = i
            break
    
    assert loop_line is not None, "Main position loop 'for p in all_open:' not found"
    
    # Look for try/except within the next 200 lines (reasonable loop body size)
    loop_body = "\n".join(lines[loop_line:loop_line + 200])
    
    # Must have try/except around position evaluation
    assert "try:" in loop_body, "No try block in position loop body"
    assert "except Exception as e:" in loop_body, "No exception guard in position loop body"
    assert "position_evaluation_failed" in loop_body or "record_error" in loop_body, (
        "Exception guard doesn't record error for failed position"
    )
    
    # Verify the fix comment is present
    assert "#1468" in loop_body, "Fix comment #1468 not found in loop body"
