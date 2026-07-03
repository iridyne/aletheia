# Learning To Operator Context Audit 2026-07-03

来源: `discussion.md` D04/D09。

边界: 本次只审计 Learning / Knowledge / analytics 如何进入 System Operator context 和
prompt。未改 Risk Engine、Execution Gateway、live unlock、凭证、下单语义。

## 1. 结论

`discussion.md` 中“Learning 不能被 System Operator 消费”的风险在当前集成分支上
不是完全成立。现有代码已经有三条已测试的 prompt 注入路径:

1. `trade_case_study` -> `trading_context.layer3.lessons` -> `trading_decision` prompt
2. `ps_lessons(status="applied")` -> `trading_context.layer3.ps_lessons` -> `Recent Post-trade Lessons`
3. `trading_memo` / `reasoning_feedback` / `calibration_feedback` -> `trading_context.layer2` -> prompt

因此 D04/D09 的准确状态应从“未完成”改为“核心消费路径已证明, 高阶自进化仍未完成”。

## 2. 已验证证据

| 证据 | 测试 | 结果 |
|---|---|---|
| trade case study 能进入 Layer3 lessons | `test_layer3_lessons_from_trade_case_study` | passed |
| applied ps_lesson 能进入 Layer3 并渲染到 prompt | `test_ps_lessons_surface_to_pi_prompt` | passed |
| Layer2 analytics 三项能渲染到 prompt | `test_a10_layer2_signals_injected_into_prompt` | passed |
| 三条 learning prompt 路径有契约枚举 | `test_pi_prompt_learning_paths_complete_enumeration` | passed |
| structured ps_lessons 走 LessonStore, 不误用 FTS | `test_trading_decision_reads_lessons_from_lesson_store` | passed |

本轮命令:

```powershell
uv run pytest tests/test_anti_hallucination_e2e.py::test_layer3_lessons_from_trade_case_study tests/test_anti_hallucination_e2e.py::test_ps_lessons_surface_to_pi_prompt tests/test_anti_hallucination_e2e.py::test_a10_layer2_signals_injected_into_prompt tests/specs/spec_learning_pipeline.py::test_pi_prompt_learning_paths_complete_enumeration tests/specs/spec_learning_pipeline.py::test_trading_decision_reads_lessons_from_lesson_store -q
```

结果: `5 passed in 0.74s`。

## 3. 修正

`tests/specs/spec_learning_pipeline.py` 的说明文字仍把 Layer3 `ps_lessons` 写成
`status="proposed"`。当前真实行为是:

```text
LessonStore.create() -> proposed
lesson_reviewer accept() -> accepted transient
mark_applied() -> applied
trading_context Layer3 reads status="applied"
```

本分支只修正测试说明文字为 `applied`, 不改变行为。

## 4. 仍未闭环的问题

| 缺口 | 状态 | 后续分支 |
|---|---|---|
| SkillStore producer | 预留架构, 无 agent/scheduler 自动 create | `audit/skillstore-tier2-gap` 或保留为 Phase 2 |
| trades analytics 更强输入 | prompt 已有 summary/memo/reasoning/calibration, 但没有完整“Closed Positions analytics block” | `fix/operator-trade-analytics-context` |
| time decay / overturn | `KL-B01` 仍 xfail, Phase 2 defer | owner 决策后做 |
| `reduce_size` hard block | 信任边界, 不能静默改 | owner 批准后 `fix/learning-reduce-size-hard-gate` |
| low-signal reviews / app.db 膨胀 | 运维/retention 决策 | owner 定 retention 后做 |

## 5. 量化状态更新

| 项 | 上一版判断 | 本次审计后 |
|---|---|---|
| Learning -> prompt 消费路径 | 部分完成/需验证 | 5 条 focused tests GREEN |
| ps_lessons 状态语义 | 文档/测试说明有 proposed 残留 | 统一为 applied |
| D04/D09 风险 | “可能未消费” | “已消费, 但高阶自进化未完成” |

下一步建议转入 `audit/universe-post-fix-distribution`, 复测 Universe 修复后的新样本分布。
