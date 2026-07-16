# 学习回路全链路修复计划

> 来源：docs/issues/audits/LEARNING_LOOP_CHAIN_AUDIT.md  
> 优先级：P0 = 无代价快修 / P1 = 核心链路 / P2 = 质量提升

---

## P0 — 断口 E：risk engine 查错 lesson 状态（5 min）

**文件**：`aletheia/risk/engine.py:366`  
**改动**：`status="accepted"` → `status="applied"`

```python
# before
active_lessons = store.list_all(status="accepted", limit=50)
# after
active_lessons = store.list_all(status="applied", limit=50)
```

**注意**：applied 行目前 recommendation_json=NULL（断口 A），所以修完仍空转，  
但正确性已建立——为 P1 打基础。

---

## P1 — 断口 A：dreaming_agent 不生成 recommendation（核心）

### P1a：在 LLM prompt 中要求 recommendation 字段

**文件**：`aletheia/agents/dreaming_agent.py:225-232`

在 system prompt 的 JSON schema 描述后追加：
```
'"recommendation" (optional object: '
'{"action": "block_scanner", "scanner": "<family>"} or '
'{"action": "block_direction", "direction": "short"|"long"} or '
'{"action": "reduce_size", "factor": 0.5} — '
'only include when evidence strongly supports a specific constraint; '
'omit if lesson is general advice without a concrete trading constraint).'
```

### P1b：把 LLM 返回的 recommendation 传给 store.create()

**文件**：`aletheia/agents/dreaming_agent.py:295-302`

```python
store.create(
    summary=...,
    source_type="trade_case_study",
    source_id=...,
    category=lesson.get("category", "general"),
    severity=...,
    evidence=evidence,
    recommendation=lesson.get("recommendation"),   # ← 新增
)
```

### P1c：在 _check_lesson_constraints 中实现 reduce_size action

**文件**：`aletheia/risk/engine.py:411`（在 block_scanner elif 后追加）：

```python
elif action == "reduce_size":
    # Advisory only — log, does not block
    factor = float(rec.get("factor", 1.0))
    logger.info(
        "lesson reduce_size advisory: lesson=%s factor=%.2f (not enforced)",
        lesson.lesson_id, factor,
    )
```

---

## P1d — 断口 G：active guards 缺惩罚值

**文件**：`aletheia/analytics/guard_promotion.py:53`

在 `_update_guard_status(guard_id, "active")` 前写入 default 惩罚：

```python
if loss_rate >= 0.6:
    if _promote_guard_with_penalty(guard, loss_rate):
        promotions += 1
```

新增 `_promote_guard_with_penalty(guard, loss_rate)` 函数：
- 读文件、更新 frontmatter：`status: active`，`score_penalty: 10.0`，`enforcement: hard_block`
- 如果文件在 guard_candidates/ 目录，mv 到 guards/ 目录

**同时**：补一次性脚本修复 8 个现有 active guard：
```python
# scripts/patch_active_guard_penalties.py
# 遍历 data/knowledge/guards/*.md
# 写入 score_penalty: 10.0, enforcement: hard_block (若原值为 0 或缺失)
```

---

## P2 — 断口 D：trade_case_study content=NULL

**文件**：`aletheia/agent_shell/trading_context.py:498-503`

在 `content` 为空时 fallback 读磁盘文件：

```python
raw_content = item.get("content") or item.get("summary") or ""
if not raw_content:
    fpath = item.get("file_path")
    if fpath:
        try:
            from pathlib import Path
            from aletheia.knowledge.frontmatter import parse_frontmatter
            meta, body = parse_frontmatter(Path(fpath))
            parts = []
            if meta.get("outcome"):
                parts.append(f"outcome={meta['outcome']} pnl={meta.get('realized_pnl_pct','?')}%")
            if meta.get("factor_key"):
                parts.append(f"factor={meta['factor_key']} scanner={meta.get('scanner_family','?')}")
            if body:
                parts.append(body[:200])
            raw_content = " | ".join(parts)
        except Exception:
            raw_content = item.get("title", "")

lessons.append({
    "type": item.get("item_type"),
    "symbol": meta.get("symbol"),
    "content": raw_content[:300],
    "source": meta.get("source_trade"),
})
```

---

## P2 — 断口 F：market_observer guard_context 缺字段

**文件**：`aletheia/agents/market_observer.py:530-537`

market_observer 调用 `build_advisory_context` 时，guard_context 补充 universe snapshot 中的  
scanner_family/direction。由于 market_observer 一次看多个 symbol，guard advisory 应以 per-symbol 形式。

**方案**：在 `_build_guard_advisory` 内，若 guard 的 `applies_to.scanner_family` 非空但 context 缺失，  
只检查 `asset_class` 做宽松匹配，并在 advisory 文本里说明这是基于 asset_class 的通用 advisory。

---

## 实施顺序

1. P0（断口 E，2 行改动）→ 立即提交
2. P1a+P1b+P1c（断口 A，dreaming_agent + engine）→ 同次提交
3. P1d（断口 G，guard promotion + 修复脚本）→ 提交
4. P2（断口 D，content 读文件）→ 提交
5. 断口 F → 推迟（market_observer 需要 per-symbol context 重构，影响面较大）

---

## 验证检查

- [ ] `uv run pytest tests/specs/ -q` 全绿
- [ ] `uv run aletheia doctor` — loop.guard_promotion: 8 active guards / lesson_check: applied=16
- [ ] dreaming_agent 下次运行后，DB ps_lessons 中出现 recommendation_json IS NOT NULL 的行
- [ ] risk engine 日志出现 `lesson_block_scanner:` 或 `lesson reduce_size advisory:` 字符串
