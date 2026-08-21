# M3 Runtime/Diagnostics Fixes

## 完成的修改

### #1213 — Ranker notification pause-aware
- scheduler.py: `notify_paper_trader` 檢查 `_selectively_paused_jobs`, 若 paused 則加入 `_pending_wake_jobs` 而非直接 arm
- `resume_scheduler` 消費 `_pending_wake_jobs` 並在恢復後觸發延遲的 wake

### #1236 — 使用規範的 unresolved queue query
- pipeline_progress.py: `_pending_stats` 改用 `count_unresolved_signal_pending()` (repositories.py 已有), 取代原始 raw COUNT

### #1237 — ProposalRationaleDetail crash-safe
- ProposalRationaleDetail.tsx: 刪除本地重複 parser, 改用 `decisionReasoning.ts` 的 `parseDecisionRationale()` + `stringList()`
- guards 錯誤格式視為 absent, 不拋異常

### #1243 — Prune failure reporting
- scheduler.py: `_prune_news`, `_prune_session_logs`, `_prune_task_notes` 改回傳 `{"deleted": int, "error": str|None}`
- `_run_cleanup` 收集 prune_errors, 有錯誤時記錄 partial failure

### #1252 — 傳播 batch exclusions
- profit_loop_diagnosis.py: `diagnose_candidate_report` 讀取 `unavailable_count` / `unavailable_reasons`, 加入診斷輸出; 當 `unavailable_count > 0` 時設 `status="partial"` + note

### #1253 — 驗證 epoch hash (部分)
- profit_loop_counterfactual.py: 加 `effective_values_hash` 查詢, 重算 hash 並驗證; 檢查 `path_id` 匹配

### #1265 — Docs v1→v2
- 兩份 contract doc 已用 sed 更新 v1→v2 reference

### #1323 — Sizing trace margin policy (未完成)
- 需在 `_risk_cap_hit` 讀 `cfg.risk.<mode>.capital.max_position_margin_usdt`

## 未完成
- #1323 sizing_trace.py 改用當前 margin policy (請求預算用盡)
- 測試尚未執行
