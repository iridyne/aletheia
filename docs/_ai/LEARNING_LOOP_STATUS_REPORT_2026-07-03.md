# Learning Loop 状态进展报告（2026-07-03）

## 结论

Learning 已经不是空页面或单纯日志展示。当前系统具备一条可运行的学习闭环：

1. 平仓/成交/机会归因进入后端。
2. `trade_case_study` 把真实交易过程整理成可复用案例。
3. Dreaming Agent 从案例中提炼 `ps_lessons`。
4. Lesson Reviewer 把证据足够的 lesson 变成 active policy。
5. System Operator、机会排序器、Risk Engine 会读取这些学习结果。
6. 前端 Learning 页面能看到 Dreaming、Knowledge、Post-Trade Lessons、Guard Candidates、Active Guards 和学习漏斗。

但它还不能被描述成“完整自我进化系统”。目前是“可学习、可反馈、局部可自修复”的状态，距离 discussion 中要求的“持续从市场信息和历史交易中重构规则、发现矛盾、淘汰旧规则、形成长期技能”还差 3 个关键点：历史 scanner_family 覆盖补齐、矛盾检测、SkillStore/时间衰减机制。

## 当前量化状态

数据来自本机 `data/app.db`，查询时间为 2026-07-03。

| 指标 | 当前值 | 说明 |
| --- | ---: | --- |
| `knowledge_items` 数据库大小 | 431.6 MB | 学习/审计数据已经有明显体量 |
| active `trade_case_study` | 168 条 | 已经有可被 Learning 使用的交易案例；所有已知 synthetic 历史污染已软归档 |
| active high-confidence `trade_case_study` | 75 条 | 当前真正会进入 FTS 学习检索的交易案例 |
| high-confidence case study 带 symbol/direction | 75 / 75 条 | symbol/direction 过滤覆盖完整 |
| high-confidence case study 带真实 scanner_family | 9 / 75 条 | 历史 scanner_family 覆盖不足，已用同 symbol/direction fallback 缓解 |
| active `guard_candidate` | 131 条 | 已经能自动产生候选防守规则 |
| active high-confidence research notes | 397 条 | 市场研究知识已进入知识库 |
| active strategy memory | 56 条 | 策略记忆有持久化 |
| active daily review | 12 条 | 日度复盘存在 |
| `ps_lessons` proposed | 67 条 | 候选 lesson 已形成 |
| `ps_lessons` applied | 25 条 | 已有 lesson 被应用到策略/风控读取路径 |
| lesson policy versions | 7 个 active | 说明 lesson reviewer 已经产出可追踪版本 |
| FTS 可检索知识 | 566 条 | high-confidence active 可学习池已与 FTS 对齐 |
| active high-confidence 缺正文知识 | 0 条 | 55 条历史空正文 `operator_assessment` 已降级退出 FTS 期望池 |
| `retrieve_lessons` 工具调用 | 2006 次 | agent 已经实际调用学习检索 |
| `build_case_studies` 调用 | 566 次 | 交易案例构建器持续运行 |
| `detect_guard_candidates` 调用 | 566 次 | guard 候选生成持续运行 |
| Learning 相关测试 | 79 passed, 3 skipped, 1 xfailed | xfail 是已知的 time-decay 缺口 |

## 已经实现的能力

### 1. 从交易历史中学习

已实现。`post_trade_reviewer` 和 `trade_case_builder` 会从历史成交、平仓原因、机会归因、策略 ID、scanner family 等数据生成案例。高质量案例会进入 `trade_case_study`，并被后续 agent 读取。

关键证据：

- `trade_case_builder` 已过滤新的 `reconciled_gone` / `demo_validation_cleanup` 这类非真实关闭原因。
- `trade_case_study` 进入 System Operator 的 Layer3 wisdom。
- `paper_trader_ranker` 会检索亏损案例，对相似机会降权。
- `risk_engine` 会读取 applied lessons，对 `block_symbol` / `block_direction` / `block_scanner` 进行约束。

### 2. 从经验中形成可执行 lesson

已实现一半以上。Dreaming Agent 会读取 `trade_case_study`，提炼结构化 lesson，Lesson Reviewer 会根据证据进行 promoted / rejected / demoted，并写入 policy version。

这说明系统已经可以把“复盘文本”推进到“可执行规则”的阶段，而不是只把复盘存在 Markdown 里。

### 3. 前端展示 Learning 状态

已实现监控 + lesson 审核界面。Learning 页面可以展示：

- Dreaming summary
- Knowledge Base
- Post-Trade Lessons
- Guard Candidates
- Active Guards
- Learning Funnel

当前已补上两类人工确认入口：

- Post-Trade Lessons 抽屉里可以 accept / reject / archive，动作会写入事件日志并刷新 Learning/Funnel 数据。
- Guard Candidates 子页可以查看候选防守规则并手动 Promote；promote 会生成 active `trading_guard`，把原 `guard_candidate` 降为 deprecated，写入 `TRADING_GUARD_PROMOTED` 事件，不硬删候选记录。

### 4. 局部自修复

部分实现。Lesson Reviewer 已经能在证据变差时 demote accepted lessons，Knowledge Manager 禁止硬删除、支持 archive/deprecate，避免错误知识被直接抹掉。

但更高阶的自修复仍缺失：知识之间的矛盾检测还是 stub，历史污染案例不会自动清理，旧规则随时间衰减/失效的机制还没有真正完成。

## 本次发现并修复的问题

### API 白名单没有覆盖真实 Learning 类型

问题：`/api/knowledge?type=...` 只允许 5 种老类型，拒绝了 `trade_case_study`、`guard_candidate`、`trading_guard`、`trading_memo` 等真实 Learning 类型。结果是：后端已有数据，但通用 API 会返回 400，容易让前端或后续 AI 误判“Learning 没数据”。

修复：

- 补齐 `VALID_KNOWLEDGE_TYPES`，覆盖 Learning 合约中的真实类型。
- 新增回归测试，确认 Learning 类型返回 200，未知类型仍返回 400。

量化结果：

- 修复前：`/api/knowledge?type=trade_case_study` 返回 400。
- 修复后：测试覆盖 4 个 Learning 类型，全部返回 200。
- 未放宽安全边界：未知类型仍返回 400。

### 历史 synthetic case study 污染 Learning 检索

虽然新代码已经阻止新的 `reconciled_gone` / `demo_validation_cleanup` 案例进入学习语料，但历史数据仍留在 active/FTS 里。它们不是交易经验，却会被检索出来影响 System Operator、ranker 或 Dreaming。

修复：

- 新增 `archive_synthetic_trade_case_studies()` 维护函数。
- 新增 CLI：`uv run aletheia knowledge archive-synthetic-case-studies`。
- 只做软归档：`active -> archived`，不硬删除。
- 执行后重建 FTS，让污染案例退出全文检索。
- 增加回归测试，证明 synthetic 案例会归档、真实案例保持 active、命令可重复执行。

量化结果：

| 项目 | 数量 |
| --- | ---: |
| 清理前 active `trade_case_study` 总数 | 267 |
| 清理后 active `trade_case_study` 总数 | 174 |
| active 中带 synthetic close reason 的案例 | 93 -> 0 |
| 仍在 FTS 检索索引中的 synthetic active 案例 | 46 -> 0 |
| 本次软归档 synthetic case study | 93 |
| 硬删除 | 0 |
| FTS 重建后索引总量 | 569 |

执行记录：

```text
dry-run: {'matched': 93, 'archived': 0, 'remaining_active': 93, 'reindexed': None, 'dry_run': True}
real-run: {'matched': 93, 'archived': 93, 'remaining_active': 0, 'reindexed': 569, 'dry_run': False}
```

补充清理（2026-07-03）：维护命令最初只覆盖 `reconciled_gone` / `demo_validation_cleanup`，后来发现统一 synthetic close reason 清单还包括 `units_bug_cleanup` / `retry_after_fix`。已修复维护命令，改为复用 `SYNTHETIC_CLOSE_REASONS` 单一来源，并再次执行软归档。

补充量化结果：

| 项目 | 数量 |
| --- | ---: |
| `units_bug_cleanup` active case study | 6 -> 0 |
| `units_bug_cleanup` FTS active case study | 3 -> 0 |
| `retry_after_fix` active case study | 0 -> 0 |
| 4 类 synthetic close reason active case study | 全部 0 |
| 4 类 synthetic close reason FTS active case study | 全部 0 |
| 第二次 FTS 重建后索引总量 | 566 |

### Learning 检索缺少结构化过滤

问题：`retrieve_lessons("BTC/USDT long momentum")` 过去只做全文 token 匹配，会把 SOL、DOGE、FIL、AAVE 等其他币种案例召回。机会排序器虽然把 `symbol + direction + scanner_family` 拼进 query，但检索层没有把它们当成硬条件。

修复：

- `retrieve_lessons()` 新增 `symbol` / `direction` / `scanner_family` 可选过滤参数。
- FTS 仍负责候选召回，但返回前会读取 canonical knowledge item 的 frontmatter，按结构化字段做二次过滤。
- 机会排序器 `_lesson_penalty()` 现在把机会的 symbol、direction、scanner_family 明确传入检索层。
- 修复新建 `trade_case_study` 时 FTS `outcome` 可能为空的问题：创建时如果正文无 frontmatter，会回读已写入文件的 frontmatter，确保亏损/盈利结果立即可用于 ranker。

量化结果：

| 查询 | 修复前/未过滤召回 | 修复后结构化过滤 |
| --- | --- | --- |
| `BTC/USDT long momentum` | 5 条，来自 SOL/DOGE/FIL/AAVE | 0 条，因当前没有 BTC long momentum 高置信案例 |
| `BTC/USDT short stop_loss` | 5 条，混入 DOGE/ETH/SOL/FIL | 2 条，均为 BTC/USDT short |
| `SOL/USDT long momentum` | 5 条，混入 DOGE/FIL/AAVE | 0 条，因现有 SOL momentum 案例是 medium_confidence，不进入 FTS |

这次修复的意义不是“返回更多”，而是“宁可没有足够证据，也不拿别的币种/方向/来源当证据”。这符合 Learning 的语义诚实要求。

### 当前架构下的 case study 质量门修正

问题：`trade_case_builder` 仍把 `strategy_id` 作为 high-confidence 的必要条件。但 Strategy/RuleSet 引擎已经移除，新的短线交易链路不再稳定携带 `strategy_id`。这会让有 proposal/opportunity/scanner 的真实交易案例被降成 medium-confidence，无法进入 FTS。

修复：

- `has_full_chain` 从 `proposal_id + opportunity_id + strategy_id` 改为 `proposal_id + opportunity_id`。
- 理由：proposal 证明交易执行链，opportunity 证明信号来源链；strategy_id 不再是当前架构的必要归因字段。
- 增加测试，锁住“无 strategy_id 但有 proposal/opportunity 仍可作为完整归因案例”。

同时，为了让历史 case study 在 scanner_family 覆盖不足时仍有价值：

- `_lesson_penalty()` 先查同 `symbol + direction + scanner_family`。
- 如果没有结果，再 fallback 到同 `symbol + direction`。
- 不会 fallback 到其他 symbol 或其他 direction。

真实采样：

| 查询 | scanner 精确结果 | fallback 结果 | Ranker 处罚 |
| --- | ---: | ---: | ---: |
| `SOL/USDT long momentum` | 0 条 | 5 条同 SOL long | -13.880216 |
| `BTC/USDT short momentum` | 0 条 | 2 条同 BTC short | -6.996936 |
| `BTC/USDT long momentum` | 0 条 | 1 条同 BTC long scratch | 0 |

### Learning lesson 管理动作缺失

问题：Learning 前端过去只能查看 post-trade lessons，不能人工确认、拒绝或归档。后端 `LessonStore` 虽然已有 `accept/reject/archive` 方法，但没有 API，也没有前端入口。这导致 owner 无法参与 Learning 自修复闭环。

修复：

- 新增 API：
  - `POST /api/persistence/lessons/{lesson_id}/accept`
  - `POST /api/persistence/lessons/{lesson_id}/reject`
  - `POST /api/persistence/lessons/{lesson_id}/archive`
- `LessonStore` 状态变更写入事件日志：
  - `LESSON_ACCEPTED`
  - `LESSON_REJECTED`
  - `LESSON_ARCHIVED`
  - `LESSON_APPLIED`
- 前端 Learning → Knowledge Base → Post-Trade Lessons 抽屉新增 `Accept / Reject / Archive` 按钮。
- 操作成功后刷新 lesson 列表、Learning Funnel 和 Learning pulse。

量化结果：

| 项目 | 结果 |
| --- | --- |
| 新增 lesson 管理 API | 3 个 |
| 新增前端 lesson 操作 | 3 个 |
| lesson 状态事件 | 4 类 |
| 后端回归测试 | 19 passed |
| 前端 build | passed |

## 仍需处理的问题

### P1：历史 case study 的 scanner_family 覆盖不足

当前 75 条 active high-confidence case study 全部带 symbol/direction，但只有 9 条带真实 scanner_family。结构化过滤修复后，scanner_family 维度会更诚实，也会暴露历史数据覆盖不足。

影响：对于 scanner_family 明确的机会，系统可能因为历史案例缺少同 scanner_family 证据而返回 0 条 lesson。这不会污染判断，但会降低可学习样本量。

建议：对历史 case study 做一次只读归因补齐或降级，目标是把真实 scanner_family 覆盖率从 9/78 提升到可解释水平；不能补齐的不要伪造，应保留 unknown。

### P1：矛盾检测仍是空实现

`detect_contradictions()` 目前仍返回空列表。文档已经明确要求 UI 不能把它展示成“无矛盾”，只能说“未检测”。

影响：系统还不会主动发现“旧规则和新证据冲突”。

### 已修复：guard_candidate 人工晋升入口

前端现在可以审核 lesson，也可以在 Guard Candidates 子页把 guard_candidate 人工确认晋升为 trading_guard。

修复：

- 新增 `/api/knowledge/guard-candidates`，让前端能看到待审核候选。
- 新增 `/api/knowledge/guard-candidates/{guard_id}/promote`，只在人工点击时晋升。
- 底层 promote 改为“创建 active guard + 原候选 deprecated + 事件记录”，避免旧逻辑把候选文件移动后留下断开的数据库记录。

量化结果：

| 项目 | 结果 |
| --- | ---: |
| 新增后端接口 | 2 个 |
| 新增前端审核入口 | 1 个 |
| 新增回归测试 | 2 个 |
| promote 后 hard delete | 0 |
| promote 审计事件 | 1 条 `TRADING_GUARD_PROMOTED` |

### P2：SkillStore producer 和 time-decay 仍缺失

当前 SkillStore 没有稳定 producer，time-decay overturn 仍是已知 xfail。这意味着系统已经能产生“规则”，但还没有形成长期“技能库”和自然衰减机制。

### 部分 high-confidence 知识缺少可重建正文

问题：FTS 重建时报告 `skipped 55 high_confidence items with empty body`。核查后发现 55 条全是历史 `operator_assessment`，没有 `file_path`，也没有 DB `content`。这些记录是审计 stub，不是可学习正文，却被 high-confidence 质量门计入 FTS 期望池。

修复：

- 新增维护函数 `downgrade_unindexable_high_confidence_items()`。
- 新增 CLI：`uv run aletheia knowledge downgrade-unindexable-high-confidence`。
- 只降级 confidence：`high_confidence -> medium_confidence`，不删除记录。
- 重新构建 FTS，使 high-confidence 可学习池与 FTS 对齐。

量化结果：

| 项目 | 数量 |
| --- | ---: |
| dry-run 命中缺正文 high-confidence | 55 |
| 实际降级 | 55 |
| hard delete | 0 |
| active high-confidence 缺正文知识 | 55 -> 0 |
| active high-confidence 知识总数 | 621 -> 566 |
| FTS 总量 | 566 |
| FTS skipped empty body warning | 有 -> 无 |

这不是删除学习材料，而是把不可学习的空记录退出“高置信可学习池”。

## 下一步建议

1. 补齐或降级历史 case study 的 scanner_family 覆盖，目标是减少结构化过滤后的“无证据”结果。
2. 实现矛盾检测，把冲突知识标记为需要复核，而不是默认无冲突。
3. 完成 SkillStore producer 和 time-decay，让 Learning 从“经验规则”升级到“长期技能”。

## 验证记录

已运行：

```powershell
uv run pytest tests/test_learning_stores.py tests/test_learning_loop_integration.py tests/test_knowledge.py tests/test_knowledge_router.py tests/test_knowledge_maintenance.py tests/specs/spec_learning_pipeline.py tests/specs/spec_lesson_guards.py tests/specs/spec_knowledge_fts.py -q
uv run pytest tests/test_server.py::test_guard_candidate_can_be_listed_and_manually_promoted tests/test_server.py::test_guard_candidate_promote_rejects_missing_guard tests/test_server.py::test_lesson_management_actions -q
uv run pytest tests/test_guard_analytics.py tests/e2e/test_pipeline_guard_promotion.py tests/specs/spec_learning_pipeline.py::test_guard_candidates_reachable_by_promoter -q
cd frontend; npm run build
```

结果：

```text
79 passed, 3 skipped, 1 xfailed in 5.90s
```

新增 API 白名单回归测试：

```powershell
uv run pytest tests/test_server.py::test_knowledge_route_accepts_learning_item_types tests/test_server.py::test_knowledge_route_rejects_unknown_item_type -q
```

结果：

```text
2 passed in 1.74s
```

新增 synthetic case study 软归档维护测试：

```powershell
uv run pytest tests/test_knowledge_maintenance.py tests/test_fts_coverage.py tests/test_experience_loop.py -q
```

结果：

```text
20 passed in 3.59s
```

新增结构化 Learning 检索测试：

```powershell
uv run pytest tests/test_fts_coverage.py tests/test_learning_loop_integration.py tests/test_experience_loop.py tests/test_knowledge_maintenance.py -q
```

结果：

```text
26 passed in 3.67s
```

新增当前架构归因门和 fallback 测试：

```powershell
uv run pytest tests/test_knowledge_maintenance.py tests/test_trade_case_builder.py tests/test_learning_loop_integration.py tests/test_fts_coverage.py tests/test_experience_loop.py -q
```

结果：

```text
56 passed in 3.69s
```

新增不可索引高置信知识维护测试：

```powershell
uv run pytest tests/test_knowledge_maintenance.py tests/test_fts_coverage.py tests/specs/spec_knowledge_fts.py -q
```

结果：

```text
18 passed in 1.00s
```

新增 lesson 管理动作测试与前端构建：

```powershell
uv run pytest tests/test_server.py::test_lesson_management_actions tests/test_server.py::test_lesson_management_action_rejects_unknown_lesson tests/test_learning_stores.py -q
cd frontend && npm run build
```

结果：

```text
19 passed in 1.42s
frontend build passed
```

补充 API 采样：

| 接口 | 结果 |
| --- | --- |
| `/api/analytics/learning-funnel` | repeated direct/frontdoor samples 全部 200，平均 4.8-19.6ms |
| `/api/dreaming/summary` | 200 |
| `/api/persistence/lessons?limit=20` | 200 |
| `/api/knowledge/guards` | 200 |
| `/api/knowledge?type=trade_case_study` | 本次修复前 400，修复后由测试覆盖为 200 |
| `/api/knowledge?type=trade_case_study` | 清理并重启 gateway 后 200，84ms |
| `/api/analytics/learning-funnel` | 清理并重启 gateway 后 200，16ms |
| `/api/knowledge?type=trade_case_study` | 结构化检索修复并重启 gateway 后 200，94ms |
| `/api/analytics/learning-funnel` | 结构化检索修复并重启 gateway 后 200，30ms |
| `/api/knowledge?type=trade_case_study` | 不可索引高置信维护后 200，98ms |
| `/api/analytics/learning-funnel` | 不可索引高置信维护后 200，16ms |
| `/api/persistence/lessons?limit=3` | lesson 管理动作上线后 200，99ms |
| `/api/analytics/learning-funnel` | lesson 管理动作上线后 200，16ms |
| `/api/knowledge?type=trade_case_study` | lesson 管理动作上线后 200，7ms |
