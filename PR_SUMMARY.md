# Fix operator queue and models issues

**Branch**: `fix/high-w4a-operator-models` (from master)  
**Issues**: #1835 #1840 #1846 #1912 #1948 #1963  
**Label**: `do-not-auto-merge`

## Changes

### Operator fixes

#### 1. Session tree cycle detection (`aletheia/server/routes/operator.py`)
- **Problem**: `_session_belongs_to_operator_tree` could loop infinitely if `parent_session_id` forms a cycle
- **Fix**: 
  - Guard empty `session_id`/`main_session_id` inputs upfront
  - Enforce max depth limit (100) to prevent unbounded traversal
  - Early-return false when inputs are empty

#### 2. Task ownership tracking (`aletheia/agent_shell/operator_store.py`)
- **Problem**: `update_task` modified task status but didn't update `runtime_ownership` metadata, causing stale ownership state
- **Fix**: 
  - Call `_attach_runtime_ownership` when status changes
  - Update metadata with new `task_type` and `status`
  - Ensures ownership table stays consistent across task lifecycle

### Model provider fixes

#### 3. Fallback boundary check (`aletheia/model_provider/fallback.py`)
- **Problem**: `models_to_try[idx + 1]` access in warning log assumed idx+1 was in bounds (already validated by outer condition but fragile)
- **Fix**: 
  - Explicit bounds check: `idx + 1 < len(models_to_try)`
  - Use `"unknown"` placeholder when next model doesn't exist
  - Safer against future refactors

#### 4. Health cache cleanup (`aletheia/model_provider/router.py`)
- **Problem**: `healthcheck_all_with_metadata` cleared `_health_inflight` only in `finally` block; exception paths could leave stale Future blocking subsequent checks
- **Fix**: 
  - Clear `_health_inflight` inside BOTH `CancelledError` and `BaseException` handlers BEFORE raising
  - Prevents inflight leak when exception happens during probe
  - Maintains single-flight correctness

## Tests

`tests/test_high_w4a_operator_models.py` validates:
- Task semantic hash consistency (input order independence)
- Session tree membership and cycle detection
- Model fallback cooldown handling
- Safe healthcheck error handling
- Task ownership runtime tracking
- Model health cache single-flight mechanism
- Context query validation
- Fallback empty candidates handling

**Test results**:
```
tests/test_high_w4a_operator_models.py — 13 passed
tests/test_operator_store.py — 6 passed (existing tests)
tests/test_model_fallback.py — 19 passed (existing tests)
```

**Import check**: All modified modules import successfully

## Safety

- No changes to execution paths (SAFETY_AND_TRUST.md compliant)
- No changes to risk, credentials, auth, or gateway logic
- Only defensive guards and metadata consistency fixes
- All changes are within operator control-plane and model provider routing
- Existing behavior preserved; fixes only prevent edge-case failures

## Next steps

1. Push branch to origin
2. Create PR with title: `fix(operator,models): task queue ownership, session cycles, model fallback bounds, health cache (#1835 #1840 #1846 #1912 #1948 #1963)`
3. Add label `do-not-auto-merge`
4. Request review from team
