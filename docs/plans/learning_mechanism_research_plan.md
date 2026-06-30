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
