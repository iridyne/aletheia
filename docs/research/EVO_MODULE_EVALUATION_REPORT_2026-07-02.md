# EVO 模块评估报告 — Strategy / Serenity Lens / Learning

> 评估日期: 2026-07-02
> 评估范围: letheia/trading/strategy_lifecycle.py、letheia/trading/serenity_evidence.py、letheia/trading/serenity_scoring.py、letheia/learning/lessons.py、letheia/learning/skills.py
> 参照标准: STD 文档体系 (DOC_SYSTEM_PROMOTION_GATES_CONTRACT、knowledge_contract)、SDD spec_*.py claims、BACKEND_ISSUES.md

---

## 1. Strategy 模块 (letheia/trading/strategy_lifecycle.py)

### 1.1 功能与设计符合度

| 维度 | 状态 | 说明 |
|------|------|------|
| 生命周期状态机 | ✅ 符合 | 10 状态 + FORWARD_TRANSITIONS + 逃生口(retired/deprecated),与 promotion_gates_contract §1 100% 吻合 |
| 晋级门阈值 | ✅ 符合 | 7 个 DEFAULT_PROMOTION_GATES 键值与契约文档完全一致 (PG-S03) |
| 关键风险标记 | ✅ 符合 | CRITICAL_RISK_TOKENS 9 个标记全部覆盖 (PG-S04) |
| 前向唯一性 | ✅ 符合 | promote_strategy() 拒绝跳级/回退/非法目标 (PG-B01) |
| 门计算数据源 | ✅ 符合 | check_promotion_gates 从 repository 查询 (orders/fills/positions/risk_decisions),非状态标签 (PG-C01) |
| 实盘晋级双门 | ✅ 符合 | live_candidate→live_limited 额外校验 live.state + first_live_confirmation + permission_mode |
| 审计日志 | ✅ 符合 | write_audit() 记录 actor/source/entity_type/entity_id/state_change |
| PI 审批门 | ✅ 符合 | paper_running 晋级需 pi_decisions.action='recommend_promote' (偏差2 已修复) |

### 1.2 已知偏差与问题

| # | 严重度 | 问题 | 位置 | 说明 |
|---|--------|------|------|------|
| S1 | 🟡 | Position C 遗产 — 无活晋级路径 | strategy_lifecycle.py 全文 | 规则引擎删除后,strategy_designer/backtest_orchestrator/operator create+promote/前端 StrategyLifecycle 页均已删除。函数保留为 legacy,但无生产路径 wire 到晋级。promotion_gates_contract.md 文首横幅已标注。**非 bug — 是 Position C 的预期后果**。 |
| S2 | 🟡 | PG-B06 spec claim 失效 | spec_promotion_gates.py:PG-B06 | 该 claim 断言 check_promotion_gates 对"有 ruleset 但零 Path-A fill"策略产出 uleset_never_evaluated 失败标记。但规则引擎已删除(PG-B06 注释本身也承认),代码中无此 gate。**spec claim 与代码现实不符 — 测试会 FAIL**。 |
| S3 | 🟠 | DEFAULT_PROMOTION_GATES 硬编码,未在 config.yaml 暴露 | strategy_lifecycle.py:55-63 | CLAUDE.md Config↔UI 规则要求每个 config 字段有前端控件。当前有意 MVP 简化,但 promotion_gates_contract §6 自己也标注此偏差。 |
| S4 | 🟠 | max_drawdown or 1.0 fallback 把容量 0 退化成 100% drawdown | strategy_lifecycle.py:319 | BACKEND_ISSUES §5.5: 任何把 max_live_limited_cap_usdt 设为 0 的部署,drawdown=PnL×100%,立即 max_drawdown_exceeded — 晋级失败原因是数学伪影。 |
| S5 | 🟡 | StrategyRecord.parameters 字段从未被生产调用者填充 | models.py:264 / strategy_lifecycle.py:97 | create_strategy 接受 parameters 参数并 json.dumps 持久化,但 strategy_designer 已删除,无调用者传入非空 parameters。trust §4.2 要求"当时这个策略跑的是什么参数"可审计 — 当前不可审计。 |

### 1.3 测试覆盖

- 	est_strategy_lifecycle.py (524 行): 覆盖前向晋级、回退阻断、跳级阻断、完整生命周期、paper 证据隔离、live_limited 双门、permission_mode 门、critical risk violation 门
- spec_promotion_gates.py: 7 个 claims (L2a/L2b/L2c),6 个 status="implemented",1 个 (PG-B06) 实际失效
- spec_strategy_pool.py: 7 个 claims (SP-S01~S07),覆盖 KPI 暴露、short-bias scanner、regime scoring config-driven

### 1.4 STD 文档对齐

| 文档 | 对齐状态 |
|------|---------|
| promotion_gates_contract.md | ✅ 100% 代码吻合 (偏差1 高频放松规则已补记,偏差2 PI 审批门已修复) |
| design/README.md | ✅ 已标 ⚠️ LEGACY |
| spec_promotion_gates.py | ⚠️ PG-B06 失效需修 |
| spec_strategy_pool.py | ✅ 全绿 |

---

## 2. Serenity Lens 模块 (letheia/trading/serenity_evidence.py + serenity_scoring.py)

### 2.1 功能与设计符合度

| 维度 | 状态 | 说明 |
|------|------|------|
| D2 生成者/评估者分离 | ✅ 符合 | assess_evidence_tier() 是纯确定性评估器,无 LLM/DB/IO。生成者的 self-grade 仅作 proposal,评估器只会降低不会升高 |
| 来源天花板 | ✅ 符合 | prediction_market/ict_methodology/smc_methodology/price_action/llm/model → speculative 硬顶 (SE-I01) |
| S3 来源可靠性软顶 | ✅ 符合 | news/social → likely, market_data/exchange_data → high_confidence, official/regulatory/sec/filing → disclosed |
| 外部引用降级 | ✅ 符合 | disclosed/high_confidence 无 URL → 降级到 likely (SE-I04) |
| 研究能力分级 | ✅ 符合 | assess_researchability() A/B/C 三级,独立于 LLM 自信度 (SR-S01) |
| 评分天花板 | ✅ 符合 | score_serenity_opportunity 应用 RESEARCHABILITY_CEILING (A=100, B=70, C=40) |
| 统一 tier 词汇 | ✅ 符合 | normalize_tier 映射 legacy free-form → canonical 4-tier ladder (SE-C02) |
| 加密-only 符号过滤 | ✅ 符合 | _is_tradeable_crypto_symbol() 拒绝股票/forex/聚合体 (SE-S07/I07) |
| 机会 tier 独立评估 | ✅ 符合 | assess_opportunity_tier() 只信 assessed_tier,不信 generator 的 self-grade |

### 2.2 已知偏差与问题

| # | 严重度 | 问题 | 位置 | 说明 |
|---|--------|------|------|------|
| L1 | 🟡 | 评分权重硬编码 | serenity_scoring.py:145-165 | tier_score_map / stage_score_map / avg_score_map 硬编码在函数内,非 config-driven。MVP 可接受,但无法在不改代码的情况下调优 |
| L2 | 🟡 | 无独立 L2 契约文档 | design/README.md | Serenity 评分/证据模块无独立 L2 契约。8 个 spec_*.py 全部是 "logical contract"(不在 REQUIRED_CONTRACTS),分散无统一入口 |
| L3 | ⚪ 已核实非 bug (2026-07-02) | _normalize_stage_for_score 别名分散 (可维护性瑕疵,非缺陷) | serenity_scoring.py:42-76, 203-213 | **复核结论**: 评分路径 stage_score_map (行 203-213) 已补齐所有别名 key ("order":22 / "candidate":26 / "demand":12 均在表内),`_normalize_stage_for_score` 未覆盖的别名 `.get()` 照样命中,不走 fallback;`_stage_index` (行 66-76) 自身处理全部别名 (candidate→candidate_position / order→order_night / sample→supply_chain)。**评分与阶段索引均正确,无错误行为**。别名逻辑分散在三处是可维护性瑕疵,非 bug。**不修 (不过度工程化)**。 |

### 2.3 测试覆盖

- spec_serenity_evidence.py: 10+ claims (SE-S01~S07, SE-I01~I07, SE-C01~C02)
- spec_serenity_researchability.py: 5+ claims (SR-S01~S05)
- spec_serenity_verdict.py: 5+ claims (SV-S01~S05)
- spec_serenity_attribution.py: 5+ claims (SA-S01~S05)
- spec_serenity_multi_lens.py: 5+ claims (ML-S01~S05)
- spec_serenity_holding.py: 10+ claims (SH-S01~S10)
- spec_serenity_live_readiness.py: 5+ claims
- spec_serenity_xstock_execution.py: 5+ claims
- spec_serenity_promote.py: 5+ claims

### 2.4 STD 文档对齐

| 文档 | 对齐状态 |
|------|---------|
| 8 个 spec_serenity_*.py | ✅ 全部 status="implemented" |
| design/README.md | ⚠️ Serenity 无独立 L2 契约条目 |
| promotion_gates_contract.md | ✅ 已标 ⚠️ LEGACY |

---

## 3. Learning 模块 (letheia/learning/lessons.py + skills.py)

### 3.1 功能与设计符合度

| 维度 | 状态 | 说明 |
|------|------|------|
| LessonStore CRUD | ✅ 符合 | create/get/list_all/accept/reject/mark_applied/archive 完整 |
| 状态机 | ✅ 符合 | proposed → accepted → applied / rejected / archived |
| 守卫时间戳 | ✅ 符合 | accept() 设置 activated_at / dry_run_until(+24h) / expires_at(+30d) |
| 策略版本闭环 | ✅ 符合 | mark_applied(policy_version_id) 关闭 lesson→policy 链 |
| SkillStore CRUD | ✅ 符合 | create/get/list_all/activate/deprecate 完整 |
| 权限强制 | ✅ 符合 | FORBIDDEN_LAYERS = {risk_limits, execution_gateway, credentials, live_mode, order_submission},create() 检查并 raise SkillPermissionError |
| 审计事件 | ✅ 符合 | create() 调用 get_event_store().append(make_event(...)) |
| SQLite 并发安全 | ✅ 符合 | was_in_tx 模式正确 (CLAUDE.md 三支柱) |

### 3.2 已知偏差与问题

| # | 严重度 | 问题 | 位置 | 说明 |
|---|--------|------|------|------|
| R1 | 🟠 | SkillStore 无生产者 — 空壳 | learning/skills.py 全文 | knowledge_contract §A2 已记录: SkillStore 有完整 create/activate/deprecate 生命周期,但无任何 Agent 或 Scheduler 调用。唯一写入入口是 server/routes/persistence.py 手工 REST API。Self-Evolution Tier 2/3 预留架构。 |
| R2 | 🟠 | 双轨学习路径无统一接口 | knowledge/retrieval.py + learning/lessons.py | retrieve_lessons() 读 knowledge_fts(自由文本 BM25),LessonStore 读 ps_lessons(结构化)。两条通路并行,无冲突,但无统一接口。knowledge_contract §A1 已记录。 |
| R3 | 🟡 | 无独立 L2 契约文档 | design/README.md | Learning 模块无独立 L2 契约。spec_learning_pipeline.py 是 "logical contract" 非 REQUIRED_CONTRACTS。knowledge_contract 只覆盖 LessonStore 消费路径(A1) 和 SkillStore 空壳状态(A2)。 |
| R4 | 🟡 | data_model spec 不覆盖学习表 | spec_data_model.py | ps_lessons / ps_skills / ps_policy_versions 三表在 spec_data_model.py 中无对应 claim。STD 文档体系的数据模型契约不完整。 |
| R5 | 🟡 | ps_lessons DDL 中 activated_at/dry_run_until/expires_at 不在 CREATE TABLE | migrations.py | 三列在 _ADDITIVE_COLUMNS (ALTER TABLE) 中,不在初始 CREATE TABLE。LessonStore.accept() 依赖这些列存在。run_migrations() 调用 _ensure_columns() 添加 — **功能正常,但 DDL 不自洽**,新读者可能误以为这些列不存在。 |
| R6 | 🟡 | PSLesson 模型有 artifact_path 字段但 create() 不设置 | learning/lessons.py:26 | PSLesson 模型定义 artifact_path: str | None = None,但 LessonStore.create() 不接收/设置该字段。始终为 NULL。 |
| R7 | 🟡 | PSSkill 模型有 created_from_lessons_json 字段但 create() 不设置 | learning/skills.py:56 | 同上 — 字段存在但无生产者填充。lesson→skill 晋升链未实现。 |

### 3.3 测试覆盖

- spec_learning_pipeline.py (1164 行): 15+ claims (LP-S01~S15, LP-IDEM, LP-EVIDENCE),全部 status="implemented"
- 	est_learning_loop_integration.py: 5 个测试覆盖 _lesson_penalty 逻辑
- 无直接测试 LessonStore / SkillStore CRUD 的单元测试

### 3.4 STD 文档对齐

| 文档 | 对齐状态 |
|------|---------|
| spec_learning_pipeline.py | ✅ 全部 status="implemented" |
| knowledge_contract.md | ⚠️ 仅覆盖 A1(ps_lessons 消费) + A2(ps_skills 空壳),不覆盖 SkillStore 权限/LessonStore 状态机/守卫时间戳 |
| spec_data_model.py | ❌ 不覆盖 ps_lessons/ps_skills/ps_policy_versions |
| design/README.md | ❌ 无 Learning 模块 L2 契约条目 |

---

## 4. 跨模块共性问题

| # | 严重度 | 问题 | 说明 |
|---|--------|------|------|
| X1 | 🟠 | 学习模块是三者中最弱的 | Strategy 有完整测试+契约(虽 LEGACY),Serenity 有 8 个 spec 全覆盖。Learning 的 SkillStore 是空壳、无 L2 契约、data_model spec 无覆盖、无直接 CRUD 单元测试 |
| X2 | 🟡 | 无统一 "EVO" 契约 | Strategy/Serenity/Learning 三者各自有 spec/contract,但无统一文档描述它们如何协作构成 "evolution" 系统 |
| X3 | 🟡 | Position C 遗产清理不完整 | strategy_lifecycle 的 PG-B06 spec claim 仍断言已删除的 ruleset gate;promotion_gates_contract 文首横幅已标注但 spec 未同步 |

---

## 5. Implementation Plan (按优先级)

### P0 — 阻断性问题 (需立即修)

| # | 动作 | 文件 | 说明 |
|---|------|------|------|
| P0-1 | 修复 PG-B06 spec claim | 	ests/specs/spec_promotion_gates.py | 删除或更新 test_promotion_gate_fails_when_ruleset_never_evaluated — 规则引擎已删除,该 gate 不存在。改为标注 "REMOVED (Position C)" 并更新 spec_claim status="removed" |
| P0-2 | 修复 max_drawdown or 1.0 fallback | letheia/trading/strategy_lifecycle.py:319 | 改为 fail insufficient_drawdown_capital_base 或读 risk.starting_capital_usdt。BACKEND_ISSUES §5.5 已授权 |

### P1 — 信任/安全相关 (本轮应修)

| # | 动作 | 文件 | 说明 |
|---|------|------|------|
| P1-1 | 为 Learning 模块创建 L2 契约 | docs/design/learning_contract.md | 描述 LessonStore 状态机、守卫时间戳语义、SkillStore 权限模型、FORBIDDEN_LAYERS、lesson→skill→policy 晋升链。引用 knowledge_contract §A1/A2 |
| P1-2 | 补 data_model spec 学习表 claims | 	ests/specs/spec_data_model.py | 新增 DM-S19/S20/S21 覆盖 ps_lessons/ps_skills/ps_policy_versions 三表的存在性+关键字段 |
| P1-3 | 补 LessonStore/SkillStore CRUD 单元测试 | 	ests/test_learning_stores.py | 直接测试 create/accept/mark_applied/archive + SkillPermissionError + 守卫时间戳计算 |

### P2 — 质量/一致性 (可排期)

| # | 动作 | 文件 | 说明 |
|---|------|------|------|
| P2-1 | 统一 ps_lessons DDL | letheia/storage/migrations.py | 将 activated_at/dry_run_until/expires_at 移入 CREATE TABLE,移除 _ADDITIVE_COLUMNS 中的对应条目。使 DDL 自洽 |
| P2-2 | 清理 PSLesson.artifact_path / PSSkill.created_from_lessons_json | learning/lessons.py + learning/skills.py | 如 MVP 不需要则删除字段;如需要则补 create() 参数 |
| P2-3 | 创建统一 EVO 模块设计概览 | docs/design/evolution_module_overview.md | 描述 Strategy/Serenity/Learning 三模块如何协作构成 evolution 系统。L3 计划层 |
| P2-4 | 补 DEFAULT_PROMOTION_GATES config 暴露 | config/schema.py + 前端 Settings | 按 CLAUDE.md Config↔UI 规则。需 owner 确认是否 MVP 范围 |

### P3 — 未来/预留 (暂不动)

| # | 动作 | 文件 | 说明 |
|---|------|------|------|
| P3-1 | SkillStore 生产者接入 | letheia/learning/skills.py | Self-Evolution Tier 2/3 — 需 owner 授权 + 设计文档 |
| P3-2 | 双轨学习路径统一接口 | knowledge/retrieval.py + learning/lessons.py | 需设计统一 API 合并 retrieve_lessons + LessonStore 查询 |
| P3-3 | 评分权重 config-driven | letheia/trading/serenity_scoring.py | 需 owner 确认是否 MVP 范围 |

---

## 6. 总结

| 模块 | 功能完整度 | 测试覆盖 | STD 文档对齐 | 主要风险 |
|------|-----------|---------|-------------|---------|
| Strategy | 95% (Position C 遗产) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ (PG-B06 失效) | max_drawdown 数学伪影 |
| Serenity Lens | 98% | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ (无独立 L2 契约) | 评分权重不可调 |
| Learning | 70% (SkillStore 空壳) | ⭐⭐ (仅 pipeline spec) | ⭐⭐ (无 L2 + data_model gap) | SkillStore 无生产者 |

**核心结论**: Strategy 和 Serenity Lens 模块的实现质量高,测试覆盖充分,与 STD 文档体系基本对齐。Learning 模块是三者中最弱的 — LessonStore 功能完整但 SkillStore 是空壳,且缺乏 L2 契约和 data_model spec 覆盖。建议按 P0→P1→P2 顺序修复。
