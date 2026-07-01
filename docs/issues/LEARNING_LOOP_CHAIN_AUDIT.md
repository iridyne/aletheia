# 学习回路全链路断口审计

> 审计时间：2026-07-01  
> 审计范围：上游（outcome_recorder → post_trade_reviewer → trade_case_builder → dreaming_agent）  
>           下游（evaluate_guards → ranker / market_observer / risk engine → PI 决策层）  
> 审计方法：代码 + DB 实测  
> 已修复：断口 B（trading_context 读 applied lessons）

---

## 链路全图

```
出结果
  └─ outcome_recorder.settle_due()              每15min
        └─ _maybe_generate_guard_candidates()   Tier1 guard候选 → 磁盘 guard_candidates/*.md
              └─ run_promotion_check()           auto_promote=true → 磁盘 guards/*.md (active)
                    └─ evaluate_guards()         ranker 排名 + market_observer advisory

  └─ post_trade_reviewer                        每4h
        └─ _run_case_builder_safe()
              └─ trade_case_builder.build_case_studies()
                    └─ create_trade_case_study() → DB knowledge_items + 磁盘 case_studies/*.md
                          └─ dreaming_agent._extract_lessons()  每天02:00
                                └─ LessonStore.create()         → DB ps_lessons (proposed)
                                      └─ lesson_reviewer.run()  每天03:00
                                            └─ mark_applied()   → ps_lessons (applied)

PI 决策层（trading_context → trading_decision）
  layer3.lessons     ← list_knowledge_items(type=trade_case_study)  [断口D]
  layer3.ps_lessons  ← LessonStore.list_all(status=applied)         [已修 断口B]
  layer3.guards      ← list_knowledge_items(type=guard_candidate)

风控硬拦截（risk/engine._check_lesson_constraints）
  ← LessonStore.list_all(status=accepted)       [断口E：永远0条]
  ← lesson.recommendation_json                  [断口A：永远NULL]
```

---

## 断口清单

### ✅ 断口 B — 已修复（dd0d267）
**位置**：`aletheia/agent_shell/trading_context.py:611`  
**问题**：读 `status="proposed"`（未审核噪音），改为 `status="applied"`（16条有效）。

### ✅ 断口 C — 设计如此
**位置**：`ps_policy_versions` 表  
**结论**：纯审计轨迹，无决策消费者，intentional。

---

### ❌ 断口 A — dreaming_agent 不生成 recommendation（关键）
**严重度：HIGH**  
**位置**：`aletheia/agents/dreaming_agent.py:220-303`  

双重断开：  
1. LLM prompt（:220-234）从未要求输出 `recommendation` 字段  
2. `store.create()`（:295-302）从未传入 `recommendation` 参数  

→ DB 中 339 条 ps_lessons 全部 `recommendation_json=NULL`  
→ 风控引擎的 `_check_lesson_constraints` 因此永远空转  

**修复**：见 `docs/plans/learning_loop_fixes.md` P1

---

### ❌ 断口 D — trade_case_study content=NULL（关键）
**严重度：HIGH**  
**位置**：`aletheia/agent_shell/trading_context.py:501`  

`knowledge_items` 表中 trade_case_study 行的 `content` 字段为 NULL；  
实际内容在 `file_path` 指向的磁盘 .md 文件里（含 outcome/pnl/factor_key 等量化字段）。  
`_build_layer3_wisdom` 读 `item.get("content") or item.get("summary") or item.get("title")`，  
三级 fallback 全失败，PI 最终只看到一行标题：`"Case: BTC/USDT long (stop_loss)"`，零量化细节。  

**修复**：在 content=NULL 时读 file_path 文件内容（截取 frontmatter + 正文前 300 字符）。

---

### ❌ 断口 E — risk engine 查错 lesson 状态（关键）
**严重度：HIGH**  
**位置**：`aletheia/risk/engine.py:366`  

`_check_lesson_constraints` 查 `status="accepted"`，但 accepted 是瞬态状态：  
lesson_reviewer 的同一次 run 里先 accept 后立刻 mark_applied，accepted 只存在毫秒级。  
实测：accepted=0 条，applied=16 条。  
→ 风控硬拦截永远对 0 条数据运行。  

**附**：即使修复了状态筛选，仍需同步修复断口 A（recommendation_json）才能真正生效。  
**修复**：改查 `status="applied"`；同时在 `_check_lesson_constraints` 中 applied lesson 缺少 recommendation 时记 debug 日志。

---

### ✅ 断口 F — 已修复（MO-2，2026-07-01 分支 fix/scanner-observer-audit-fixes）
**严重度：MEDIUM**  
**位置**：`aletheia/agents/market_observer.py:531`  

market_observer 调用 `build_advisory_context(guard_context={"symbols": ..., "asset_class": "crypto"})`，  
没有传 `scanner_family` 和 `direction`。  
所有 active guard 的 `applies_to.scanner_family=["momentum"]`，  
`_applies_to_context` 在 `scanner_family=None` 时直接返回 False。  
→ guard advisory block 对 market_observer LLM 永远是空字符串。  

**修复（已实施 MO-2）**：guard advisory 从循环外全局构建（用 `symbols` list，`_applies_to_context` 恒 False）移入 `_analyze_symbol`，用 per-symbol `scanner_family`（universe member 已知，LLM 前可得）经新增的 `router.build_guard_advisory()` 构建。run 级 memory_block 现只承载 symbol-agnostic lessons。spec `UP-07` 锁定。

---

### ❌ 断口 G — active guards 全缺惩罚值
**严重度：MEDIUM**  
**位置**：`data/knowledge/guards/*.md`（8 个文件）  

所有 8 个 active guard 是 outcome_recorder 自动生成的（`auto_generated: True`），  
frontmatter 缺 `score_penalty`、`block_candidate` 字段（取默认值 0.0 / False）。  
guard_promotion.py 升级时只写 `status: active`，未补充惩罚值。  
→ guard 命中但效果为 0：`penalty=0.0`，`blocked=False`。  

**修复**：guard_promotion 在 promote 时写入 `score_penalty: 10.0` 和 `enforcement: hard_block` 作为默认惩罚；  
active guard 文件补写（一次性脚本或手动）。

---

## 上游其他发现（不阻塞，记录备案）

| # | 节点 | 文件:行号 | 描述 | 严重度 |
|---|------|-----------|------|--------|
| U1 | post_trade_reviewer | `post_trade_reviewer.py:130` | LLM 不可用时整个 strategy review 跳过 | low |
| U2 | post_trade_reviewer | `post_trade_reviewer.py:104` | 用 `current_price` 代替 exit_price 传给 LLM | low |
| U3 | trade_case_builder | `trade_case_builder.py:347` | skip_existing 读文件系统而非 DB | low |
| U4 | trade_case_builder | `trade_case_builder.py:157` | detect_guard_candidates 需 ≥3 相同 factor_key，早期数据少时永远空 | 设计限制 |
| U5 | trade_case_builder | `trade_case_builder.py:284` | predicates=[] 时 candidate 静默丢弃无日志 | medium |
| ~~U6~~ | dreaming_agent | `scheduler.py:720` | ~~调度默认走 SerenityResearchRun，dreaming 只在 fallback 路径~~ → **已修(e086ad7)**：_extract_lessons 现独立于 Serenity pipeline 显式调用 | ~~high~~ 已修 |
| U7 | dreaming_agent | `dreaming_agent.py:199` | file_path 读文件：文件移动/删除时 case_lines 退化为全 `?` | medium |

---

## reduce_size action 空洞（文档声明但代码未实现）

**位置**：`aletheia/risk/engine.py:358`（docstring），代码内无 `elif action == "reduce_size"` 分支。  
即使将来有 reduce_size lesson，也会被完全忽略。需在修复断口 A 时同步实现。
