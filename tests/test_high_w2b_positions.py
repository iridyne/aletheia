"""Tests for #1468 — positions safety issues.

#1468: One position exception aborts all position evaluation
"""

import ast


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
