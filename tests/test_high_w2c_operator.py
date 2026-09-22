"""Tests for high-priority W2C operator fixes: #1435, #1539."""

import ast
from pathlib import Path


def test_operator_routes_no_blocking_async():
    """#1539: Purely synchronous routes use `def`, not `async def`.
    
    Routes with zero awaits block the event loop when declared `async def`.
    FastAPI runs `def` routes in a thread pool, unblocking the event loop.
    """
    operator_py = Path(__file__).parent.parent / "core" / "aletheia" / "server" / "routes" / "operator.py"
    source = operator_py.read_text()
    tree = ast.parse(source)
    
    # Routes that MUST be `async def` (they have awaits or are async generators)
    allowed_async = {
        "operator_status",  # await asyncio.to_thread(...)
        "operator_loop_trigger",  # await asyncio.to_thread(...)
        "operator_events_stream",  # async generator
        "operator_pause",  # await _set_paused(...)
        "operator_resume",  # await _set_paused(...)
        "operator_wake",  # await _set_paused(...)
        "operator_command_preview",  # await _preview_operator_command(...)
        "operator_command",  # has awaits
        "operator_execute_internal_command",  # await _execute_internal_command(...)
        "operator_internal_command",  # await _execute_internal_command(...)
        "operator_llm_session",  # async with awaits
    }
    
    # All operator_ routes
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("operator_"):
            # Check if it has any await expressions
            has_await = any(isinstance(n, ast.Await) for n in ast.walk(node))
            # Check if it's an async generator (has yield)
            has_yield = any(isinstance(n, ast.Yield) for n in ast.walk(node))
            
            if node.name not in allowed_async:
                assert False, (
                    f"{node.name} is `async def` but has no awaits. "
                    f"Convert to `def` so FastAPI runs it in a thread pool (issue #1539)."
                )
            elif not has_await and not has_yield:
                # Route is in allowed_async but has no actual awaits/yields
                assert False, (
                    f"{node.name} is in allowed_async but has no awaits/yields. "
                    f"Either convert to `def` or remove from allowed_async."
                )


def test_task_runtime_confirm_before_mutate():
    """#1435: Task transitions confirm server response before mutating local state.
    
    Problem: `promoteQueuedTask()` removed tasks from `queuedTasks` BEFORE confirming
    server update succeeded. Network errors left tasks "dropped".
    
    Fix: confirm server response (HTTP 200) before mutating local state.
    """
    task_runtime_ts = Path(__file__).parent.parent / "operator-shell" / "src" / "task_runtime.ts"
    source = task_runtime_ts.read_text()
    
    # promoteQueuedTask: must read queuedTasks[0], await update, THEN shift()
    # The fix comment "Confirmed on server — now safe to mutate local state" should appear
    assert "Confirmed on server — now safe to mutate local state" in source, (
        "Missing confirm-before-mutate comment in task_runtime.ts"
    )
    
    # completeTaskForRecommendation: must NOT splice() before await
    # The pattern should be: await safeUpdateTask, check result, THEN splice
    lines = source.split("\n")
    in_complete_for_rec = False
    in_fail_for_rec = False
    
    for i, line in enumerate(lines):
        if "async completeTaskForRecommendation" in line:
            in_complete_for_rec = True
        elif "async failTaskForRecommendation" in line:
            in_fail_for_rec = True
        elif in_complete_for_rec or in_fail_for_rec:
            # Look for splice patterns
            if "this.queuedTasks.splice(queuedIndex, 1)" in line:
                # Must be AFTER an `if (completed !== null)` or `if (failed !== null)` check
                # Check preceding lines for the guard
                preceding = "\n".join(lines[max(0, i - 5):i])
                assert "!== null" in preceding, (
                    f"Line {i+1}: splice() without null-check guard. "
                    f"Must confirm server response before removing from queue (issue #1435)."
                )
                in_complete_for_rec = False
                in_fail_for_rec = False


def test_task_runtime_active_task_confirm():
    """#1435: completeActiveTask/failActiveTask clear activeTask only on success."""
    task_runtime_ts = Path(__file__).parent.parent / "operator-shell" / "src" / "task_runtime.ts"
    source = task_runtime_ts.read_text()
    lines = source.split("\n")
    
    in_complete_active = False
    in_fail_active = False
    
    for i, line in enumerate(lines):
        if "async completeActiveTask" in line:
            in_complete_active = True
        elif "async failActiveTask" in line:
            in_fail_active = True
        elif (in_complete_active or in_fail_active) and "this.activeTask = null" in line:
            # Must be inside an `if (completed !== null)` or `if (failed !== null)` block
            preceding = "\n".join(lines[max(0, i - 5):i])
            assert "!== null" in preceding, (
                f"Line {i+1}: activeTask cleared without null-check guard. "
                f"Must confirm server response before clearing (issue #1435)."
            )
            in_complete_active = False
            in_fail_active = False
