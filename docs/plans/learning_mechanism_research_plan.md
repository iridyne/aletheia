# 学习/进化机制深度研究计划

状态：待启动（产品对话完成后执行）
来源：2026-06-30 产品深度对话 第4领域

## 已定案

- 学习输入：早期两路并行——个例 reflection（每笔平仓后）+ 批次模式归纳（定期）
- 学习可改范围：Ranker 权重（低风险）+ PI system prompt 片段（中风险）；风控阈值暂不开放
- 推翻机制方向：方案1（置信度更新）+ 方案2（时间衰减 half-life）+ 方案3（决策时 PI 主动过滤）三路融合，需研究后设计

## 研究阶段

### Phase 1 — 学术层（论文扫描）
- 贝叶斯参数更新在量化交易里的应用（方案1理论根基）
- 时间衰减 / half-life 模型（EMA decay、regime-conditional decay，方案2）
- Meta-cognitive filtering / RAG 式规则检索（方案3实现路径）
- 在线学习 vs 批量学习在交易系统里的失败模式

### Phase 2 — 实践层（本地参考实现）
- AlphaQuanter RL 闭环具体参数更新实现
- hermes-agent FTS5 记忆检索上下文过滤机制
- RD-Agent 因子迭代循环反馈机制
- 三者"推翻机制"共同模式提炼

### Phase 3 — 综合层（融合设计）
- 融合三方案：置信度更新 × 时间衰减 × 决策时过滤
- 输出：具体数据结构 + 触发条件 + rule 生命周期状态机
- 评估 token 成本和实现优先级

## 输出物
- `docs/research/LEARNING_MECHANISM_DESIGN_[date].md`
- 更新 `spec_data_utilization` 登记学习数据消费路由

## Spec 覆盖状态（2026-06-30 更新）

以下 spec claims 覆盖本计划的关键 defer/tripwire 节点，见 `tests/specs/spec_learning_pipeline.py`：

| Claim  | 级别 | 状态     | 覆盖内容                                         |
|--------|------|----------|--------------------------------------------------|
| KL-S01 | L2a  | GREEN    | 两路触发器独立注册验证（post_trade_reviewer interval + dreaming_daily/insight_synthesizer cron） |
| KL-S02 | L2a  | GREEN tripwire | 风控 Phase 2 防护：risk/engine.py 不得出现 learning override 接口；接口出现时 tripwire 失败触发 change-control 审查 |
| KL-S03 | L2a  | GREEN    | 批次归纳三核心 job 均已注册（insight_synthesizer / confidence_calibration / dreaming_daily） |
| KL-B01 | L2b  | XFAIL defer | 方案2 时间衰减推翻路径（half-life 模型）；Phase 2 研究完成后实现并去掉 xfail |

### Defer 节点对应关系
- **KL-B01** ↔ 本计划 §已定案"推翻机制方向·方案2（时间衰减 half-life）"；实现时需同步 `learning_mechanism_research_plan.md` Phase 2 状态 + `spec_learning_pipeline.py` KL-B01 status
- **KL-S02** ↔ 本计划 §已定案"风控阈值暂不开放"；Phase 2 正式开放风控接口前必须先过 KL-S02 审查
