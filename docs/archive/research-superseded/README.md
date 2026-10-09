# research-superseded/ - 已被取代或会误导的研究

2026-10-09 从 `docs/research/` 迁入。只收“结论已被后续代码或决策取代、继续放在
research 当前目录会被当成待办或现状”的文件；其余带日期研究仍留在 `docs/research/`。

| 文件 | 原位置 | 迁出原因 |
|---|---|---|
| `coriva_knowledge_extraction_2026-06-30.md` | `research/agents-learning/` | “P0 立即实施”清单对应的 Round 2 计划已终止，相关 α-101 代码已删除 |
| `SERENITY_DOCTOR_FALSE_WARN_2026-06-29.md` | `research/agents-learning/` | 所述误报已修复（`core/aletheia/diagnostics/probes/data_quality.py` 阈值改为 26h）|
| `EXIT_INTENT_IMPLEMENTATION_PLAN_2026-07.md` | `research/trading-strategy/` | 自标“初稿 / DRAFT”的实施计划，依赖已退役的 Operator 入场路径 |
| `RESEARCH_SHORT_AND_STRATEGY_2026-06-17.md` | `research/trading-strategy/` | “现状诊断”一节描述的 `paper_trader_ranker → paper_trader` 信号流已退役，读作现状会误导 |

冻结文件（`legacy/*`）正文中引用的旧路径不改，原路径记在此表。
