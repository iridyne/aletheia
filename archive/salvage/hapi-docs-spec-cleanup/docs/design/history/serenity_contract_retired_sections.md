# Serenity Lens Contract — retired and historical sections

> **History (moved 2026-10-10).** Retired or superseded design kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`serenity_contract.md`](../contracts/serenity_contract.md) when it was
rewritten into the numbered Contract form. Current behavior lives only in that Contract.

## Former header notes

> 契约级别: L2 — 规范性文档,实现依据。
> **产品状态: Experimental,默认关闭**(`serenity.enabled=False`、`auto_promote=False`,`config/schema.py::SerenityConfig`)。
> 产品只交易 USDT 本位加密永续:Serenity 发布 Candidate 仍须命中已登记的 exact USDT 永续 instrument。
> 本文 §5/§10 描述的持久化符号过滤放行 BTC/ETH/USDC/BUSD 计价与 `equity_tradeable` 股票代币研究分类,
> 比产品范围宽;它们不能取得入场权限,但与 futures-only 不一致,属待收敛的 conformance gap。
> 改动信任不变量前必须 change control (see trust_and_change_control.md §6)。
> Source files:
> - `core/aletheia/trading/serenity_evidence.py` (确定性证据评估器 D2)
> - `core/aletheia/trading/serenity_scoring.py` (评分卡 + bucket)
> - `extensions/research/research_worker.py` (可选多镜头研究 pipeline + 符号过滤 + 委托晋升)
> - 决策/执行/持仓层的 trade_school 穿透 (见 §8–§10)
> Cross-references: decision_lifecycle_contract.md · knowledge_contract.md · risk_engine_contract.md · live_unlock_contract.md · data_model.md
> Last updated: 2026-09-07 (D2 canonical Candidate entry)

## Historical incident behind the crypto-only filter (former invariant #5)

5. **加密-only 符号过滤**: LLM 幻觉出的非加密品种(股票/forex/聚合体)在持久化前被 `extensions/research/research_worker.py::_is_allowed_serenity_symbol()` 拒绝,不得据此取得 Candidate 入场权限 (§10)。历史事故: LLM 曾生成 MSTR/SPY/GBP/USD 写入 serenity_opps。

## Former alias robustness note (§4.3)

> **别名健壮性说明**: `_normalize_stage_for_score` / `_stage_index` / `stage_score_map` 三处各自维护 stage 别名,`stage_score_map` 已补齐所有别名 key(order/candidate/demand 等),`.get()` 命中不走 fallback。评分与阶段索引正确,别名分散是可维护性瑕疵非缺陷(EVO 评估报告 L3 已核实)。

## Former verdict note on retired PI assertions (§5)

Stage 4 **强制裁决** (S4 verdict): `_OPPORTUNITIES_SYSTEM` 强制 LLM 输出结构化 `verdict_json`(`recommendation` ∈ long/short/watch/avoid + `bull_case` + `bear_case` + `key_assumption`),消除两边倒措辞 (SV-S02/SV-B01)。裁决是研究证据，不是可执行 TradingDecision；source/hash 冻结后仍须由共享真实 PA evaluator 与 Entry Planner/Compiler 形成入场计划。历史 SV-S03/S04/B02/C01 对旧 PI context 的断言不再是当前入场权限。

## Retired Option A promotion chain (§7)

**历史（退役 Option A）**：曾经的 promote→PiDecision(signal_pending)→PI queue、
exact pending hydrate、final trade/skip carrier 去重与 orphan claim 恢复，仅解释旧审计。
这些入口现在不执行、不生成新提案。旧研究待处理项须凭新鲜真实证据重新准入，不能继承
pending 作为批准；已成交持仓和未知订单仍由现有 Gateway/持仓 owner 收口。

## Former live-readiness scope disclaimer (§9)

本入场切片不宣称已完成六种外部 mode 的 UTA 唯一私有账户切换；那是批准的后续部分，
读取、mutation 资格与实盘解锁仍需各自真实证据。

## Stock-token execution section (§10, out of product scope)

## §10 股票代币执行 (xstock)

> **出产品范围。** 股票代币与 venue 标为 `isRwa` 的永续被 registry 拉黑;本节只描述仍存在的代码分类,不是执行能力。

Serenity 直接产出的股票代币(xStocks)机会使用独立资产类别 `equity_tradeable`,与 equity_observer 的观察专用 `equity` 互不干扰:
- research_worker 的 stock-token 研究分类不是执行批准；只有已登记的 exact venue/symbol/market_type 与受支持 instrument context、真实 PA/eligibility 门可发布 Candidate。纯股票 ticker 或预测问题文本不能伪造成 instrument。
- `equity_observer` 不含 `equity_tradeable`,保持观察-only,OBS-S04 不被破坏(SX-S03)。
- 加密-only 过滤(§2 不变量 #5,SE-S07/I07): `_is_allowed_serenity_symbol` + `_ALLOWED_QUOTE_CURRENCIES` 在持久化前拒绝 MSTR/USD、SPY、GBP/USD、USDT/INR,放行 BTC/USDT、ETH/BTC 等。

## Historical spec mapping (§12)

## §12 历史 Spec 映射与当前验证

下表保留旧版9个 logical contract 的定位历史，不宣称这些旧文件或 claim 仍存在/通过。
promote→PI 队列、TradingDecision 和 broker 源码拼写断言已不代表当前入场契约：

| Spec 文件 | 契约名 | 覆盖 | claim |
|-----------|--------|------|-------|
| `spec_serenity_evidence.py` | serenity_evidence_quality | D2 分离 / tier 阶梯 / 来源天花板 / 外部引用 / 符号过滤 | SE-S01~S07, SE-I01~I07, SE-C01~C02 |
| `spec_serenity_researchability.py` | serenity_researchability | A/B/C 分级 + C 级封顶 40 | SR-S01~S03, SR-B01~B02 |
| `spec_serenity_verdict.py` | serenity_verdict | 强制 verdict_json / bear_case / key_assumptions | SV-S01~S04, SV-B01~B02, SV-C01 |
| `spec_serenity_multi_lens.py` | serenity_multi_lens | 4 镜头研究 + 合成事件 + methodology 旁路 | ML-S01~S03, ML-B01~B02 |
| `spec_serenity_attribution.py` | serenity_attribution | signal_source 归因链无 silent None | SA-S01~S05, SA-I01~I03, SA-C01 |
| `spec_serenity_promote.py` | serenity_promote | promote→PI 队列 (Option A) + D5 开关 | SP-S01~S06, SP-B01~B03 |
| `spec_serenity_holding.py` | serenity_holding | trade_school 穿透 + 长周期不被 72h 杀 | SH-S01~S10, SH-B01~B03 |
| `spec_serenity_live_readiness.py` | serenity_live_readiness | live 对称 + 信任边界隔离 | LR-S01~S06 |
| `spec_serenity_xstock_execution.py` | serenity_xstock_execution | equity_tradeable 资产类别 | SX-S01~S03 |

Research attempt lifecycle 的聚焦行为由 `tests/test_serenity_workbench.py` 和
`tests/test_serenity_scheduler_continuity.py` 覆盖：queued/running cancel race、worker
acknowledgement、terminal CAS、durable progress surface 与 restart reconciliation。

当前 publication/holding 聚焦验证：`tests/test_serenity_routes.py`、`tests/test_serenity_manual_opportunity_evidence_gate.py`、`tests/test_serenity_p0_p3_e2e.py`、`tests/test_entry_plan_execution.py`。本次文档编辑没有运行这些测试。

## Former known limitation L1 (§13)

- **L1 评分权重硬编码**: `tier_score_map` / `stage_score_map` / `avg_score_map` / `_RESEARCHABILITY_CEILING` 写死在 `serenity_scoring.py`,非 config-driven。注意区分: Serenity 的**行为参数**(持有天数/自动晋升/自动评分阈值)**已** config 化 + 前端控件(§8/§7);硬编码的仅是**内部评分映射**这一实现细节。MVP 阶段不暴露 (owner 待定)。

## Former relation row (§14)

| 契约 | 关系 |
|------|------|
| [promotion_gates_contract.md](promotion_gates_contract.md) | 历史 Option A 仅供追溯；当前 publication 见 §7，不走旧 PI 队列或 strategy_lifecycle |
