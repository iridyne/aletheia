# R20/AstraQuant：prompt 层如何承载交易 alpha

分析对象：`/Users/yixian/Projects/references/r20-quantum-trader` HEAD `642701c4`（v8.4.0）。严格只读，未修改该仓库任何文件、未执行任何 git 写操作。

**口径更正**：context 给的「`data/prompt_library.json` 435.5 KB」与实测不符。本机实测 `285,077` bytes（`ls -la`）。以下所有数字均为本机实测，命令口径逐条标注。

---

<!-- 主 Agent 独立复核记录（2026-09-28）-->
> **本文由子 Agent 产出，主 Agent 已独立复核以下关键断言，全部成立：**
>
> 1. **v8.4.0 两次提频 commit 零信号/零回测改动** —— `git show --name-only` 实测：`6188055a` 只碰 `astra_backend/risk_config.py` / `data/prompt_library.json` / `scripts/prompt_library.py` / 一个测试；`1da3ee63` 多碰前端 locale 与 `scripts/brain/*`、`scripts/risk_constants.py`。**两者均未碰 `scripts/factors/scoring.py`（实盘信号）与 `scripts/backtest_engine.py`（回测）。** 结论「换 prompt 即换策略」成立。
> 2. **置信度门实际值 80** —— `scripts/risk_constants.py:76` `MIN_ENTRY_CONFIDENCE = _env_float("ASTRA_MIN_ENTRY_CONFIDENCE", 80.0)`；而 prompt 文本里出现的阈值以 **85（28 次）/ 78（20 次）** 为主。**代码门与 prompt 标定不一致，成立。**
> 3. **`02_confidence_gatekeeper` 的 `context.min_confidence` 确为死参数** —— 生产唯一调用方 `scripts/brain/decisions.py:42-48` 构造的 `context` 只有 `active_inst_ids` 与 `active_position_sides` 两个键，**从不注入 `min_confidence`**，故 `plugins/interceptors/02_confidence_gatekeeper.py:29` 恒回落 75.0。成立。
> 4. **四道拦截器确实在生产路径上运行**（我一度怀疑它们只在测试里被调用）—— `run_interceptor_pipeline` 的非测试调用方是 `scripts/brain/decisions.py:47`，且异常时 fail-closed 到 WAIT（`:49-62`）。**「只在测试里跑」的怀疑不成立，已排除。**
>
> 未复核项见文末「本次未验证」。

## 一句话结论

R20 的 prompt 不是"策略描述"，而是**一份可被后台热编辑的伪执行规格**：它用极其具体的阈值（`P续 ≥ 46%~50%`、`0.02%~0.10%` 挂单距、`0.8R~1.2R` 止盈、`76%~88%` 置信度）把"该不该开、开多大、怎么挂"全部写死在自然语言里，而执行层只有 **4 道拦截器 + 1 处 leverage clamp** 真正复核其中一小部分——**方向、标的选择、入场价、止盈价、confidence 的具体取值全部由 LLM 自由裁量且代码完全不复核**；所以"换一版 prompt 就换一套交易行为"在 R20 上是**真的**，而且 v8.4.0 的两次提频 commit 正是**只改 prompt 词句 + 风控常量，零信号代码改动**（已实证，见下）。但它的 alpha 并不住在 prompt 的"洞察"里——住在 prompt 反复强迫模型**不要 WAIT**（提高下注频率）× 标的池只有 10 个手挑主流永续（限制了选股错误空间），而 prompt 里最精致的那部分（三阶棘轮、四大形态、概率定价）**代码根本不验证**，属于自然语言装饰。

---

## prompt 资产清点

### 文件与体积（命令：`ls -la`、`wc -l`、`python3 -c` + `json`/`ast`）

| 资产 | 实测 |
|---|---|
| `data/prompt_library.json` | 285,077 bytes |
| ├ `profiles`（2 个 profile 的活文本） | 24,305 bytes JSON / **18,978 个字符**的纯文本叶子 |
| ├ `revisions`（6 条历史快照） | 115,055 bytes / 96,652 字符 = **全文件 40%，纯历史包袱** |
| └ `version=2`, `active_profile_id="stable"` | — |
| `scripts/prompt_library.py` | 82,543 bytes / 1,234 行 |
| `scripts/ai_brain_trader.py` 内联 prompt 常量 | **6,713 字符**（`_SYSTEM_CORE` 4,743 + `_PYRAMID` 429 + `_SYSTEM_JSON_CONTRACT` 1,536） |
| `astra_backend/council/policy.py` 席位 prompt | 4,427 字符 |
| `scripts/self_improvement_engine.py:342` `EVOLUTION_SYSTEM_PROMPT` | 948 字符 |
| `docs/PROMPT_GUIDE.md` | 214 行 |

`profiles` 只有 **2 个**：`stable`（`全维度波段强化版`，active）与 `wide_oscillation`（`宽幅震荡箱体收割版`，2026-09-27 由 `1da3ee63` 新建）。`revisions` 只有 **6 条**，最后一条 `created_at = 2026-09-14 03:00:50`，全部是人工编辑留痕（`note` 字段写的是"批5 P3-4 中间态"这类开发流水），**没有一条来自自进化引擎**——这是判断自进化闭环真伪的关键物证（见第 5 节）。

### prompt 文本量级 vs 确定性决策代码量级

- **prompt 侧**：活跃投产文本 = `profiles` 18,978 字符 + 席位 4,427 + 复盘 948 ≈ **2.4 万字符**。按中文约 1 字符 ≈ 1 token 粗估 ≈ 2 万 tokens 量级（**估算公式明示：中文按字符计，非 tokenizer 实测**）。但真正每轮进模型的 system prompt 只有 `stable` 的 8 个 base 模块合计 **6,696 字符**（逐模块实测：397+1370+545+1037+723+660+428+1536），加 `custom-tu-style` 596 字符。
- **代码侧**（命令：`grep -cvE '^\s*(#|$)'`，非空非注释行）：真正参与"这单能不能开"的确定性代码合计 **2,050 行**，其中四道拦截器只有 **117 行**（25+34+21+37）；剩下是 `interceptor_manager.py` 406、`brain/decisions.py` 160、`risk_constants.py` 123、`risk_config.py` 393、`instrument_pool.py` 392、`evolution_shield.py` 459。

**关键比例**：真正对 LLM 输出做否决的逻辑 = **117 行拦截器 + `interceptor_manager.py` 的 4 道前置检查**；而告诉 LLM "该怎么想"的文本 = 6,696 字符。**否决面（4 个维度）远小于指令面（几十个阈值）**。

### `scripts/prompt_library.py` 的职责

不是渲染器，是**带白盒护栏的版本管理器 + 布局编译器**，本身不含交易逻辑：

- CRUD/版本：`create_profile:888`、`update_profile:911`、`activate_profile:979`、`rollback_profile:1004`、`import_profile:1067`、`export_profile:1020`
- 变量渲染：`render_variables:1126`——纯单次替换，`ALLOWED_VARIABLES` 白名单外返回 `[UNKNOWN_VARIABLE:key]`，缺值返回 `[MISSING_CONTEXT:key]`（`:1139-1143`）。**不递归解释替换值**（`:1131`），这是防 prompt 注入的正确做法。
- 布局编译：`apply_module_layout:1162`——把 base 模块与 profile 自定义模块按顺序拼装。
- **白盒护栏**：`_FORBIDDEN:409-416` 六条正则，拦"忽略/绕过/关闭 P0/止损/OCO/JSON 契约"与写入 API Key；`_BENIGN_PREFIX:418` 避免"严禁关闭止损"这类合规表述被误杀。`scan_forbidden:421` 在保存/导入/回滚三处复用。这是 R20 少见的**设计正确**的部分。

### base 文本的单一事实源已验证一致

`_BASE_TEMPLATE_SOURCES:480-483` 声明 `trading_system` 的基座取自 `scripts.ai_brain_trader.SYSTEM_PROMPT`。我用 `ast` 抽出代码常量、按 `==== 【标题】 ====` 切段，与 JSON 里 8 个 base 模块逐一比对：**8/8 完全相等（长度与内容全等）**。即 JSON 快照与代码文本当前无漂移，`scripts/ai_brain_trader.py:393-394` 注释声称的"标题即接口、杜绝快照漂移"成立。

---

## prompt 里写了什么

以下全部摘自 active profile `stable`（JSON 路径 `.profiles.stable.pipelines.trading_system[i].content`，与 `scripts/ai_brain_trader.py:398-522` 的 `_SYSTEM_CORE`/`_PYRAMID`/`_SYSTEM_JSON_CONTRACT` 逐字相同）。

### 类别 1：入场条件——写了大量具体阈值

`trading_system[3]`「三大底层数理基石」：

> 【胜率数学期望定价】：当条件延续概率 P续 ≥ 46%~50%（做多）或击穿概率 P破 ≥ 46%~50%（做空）…果断作为首选发单依据；
> 【概率优势定方向】：P续 显著高于 P破（差值 ≥ 8%~10%）时…若差值在 5%~8% 的弱优势区，结合 1H 微积分速度减速回踩支撑均线与加速度 a 转正企稳，亦可果断进场

`trading_system[4]`「四大王牌高胜率入场形态」：

> ① 顺势回踩均线/支撑位缩量企稳（Pullback to Value / 做多）；② 顺势空头反弹承压阻力位遇阻回落（Throwback to Resistance / 做空）；③ 假跌破流动性掠夺后迅速收回…④ 假突破流动性衰竭后迅速跌回

`trading_system[2]`「决策优先级」P2：

> 1H ADX 趋势强度（ADX ≥ 14 即可作为有效动量参与…ADX 18~22 小仓参与，ADX < 18 严禁半山腰开仓）

**代码执行情况**：`P续/P破` 阈值、四大形态、`ADX ≥ 14` 这三项，**代码零验证**。唯一与之相关的是 `plugins/interceptors/03_adx_volatility_filter.py:23` 的 `if 0 < adx < 18.0` 硬拦——注意它拦的是 18，而 prompt 说 14 可参与，**prompt 比代码松**，模型按 14 输出的单会被 18 拦掉。`continuation_prob_pct` 只作为数据被注入 `{{market_matrix}}`，**没有任何代码检查模型是否真的按它决策**。

### 类别 2：方向偏好——名义对称，机制上不对称

prompt 反复强调对称：

> `trading_system[4]`：多空双向对称顺势原则（Dual-Direction Trend Following）：多与空同等重要
> `trading_system[5]`：4H_MACRO_BULL 大级别多头通道下 100% 严禁输出 SELL_SHORT 逆势摸顶；4H_MACRO_BEAR 大级别空头承压下 100% 严禁输出 BUY_LONG 逆势抄底！

**代码只执行这一条**，且是唯一真正对称的一条：`plugins/interceptors/01_macro_trend_filter.py:23-26` 字符串包含判断 `"4H_MACRO_BULL" in macro_4h` → 拦 SELL_SHORT，`"4H_MACRO_BEAR" in macro_4h` → 拦 BUY_LONG。

**但存在结构性做多倾斜**，来自两处：

1. `trading_system[3]` 的微积分段对多头给了**额外的进场理由**，对空头没有等价条款：
   > 在 4H 顺势大浪中，1H 减速回抽正是触碰支撑均线（EMA21/55）时的极佳打折买点，当 a 由负转正、j 趋缓（回踩企稳）**必须果断顺势做多**
   而做空侧只有一句"1H 弱反弹减速遇阻正是逢高做空的极佳卖点"——没有"必须"，没有具体均线锚点。**这是 prompt 级的多空不对称，纯文本造成的。**
2. `scripts/evolution_shield.py:97-98` 的 `POISON_PATTERNS` 前两条**同时**禁止"永远不做多"和"永远不做空"写入记忆（注释 `:95` 明确写"方向偏见两条不豁免"）。设计是对的，但它只管自进化写回的记忆，**管不了人工编辑的 profile 文本**。

### 类别 3：仓位/杠杆/止损——prompt 主动放弃，交给代码插值

这是 R20 做得最干净的一处。prompt 里**没有任何写死的绝对金额**：

> `trading_system[0]`：一切金额类参数（保证金、风险额、熔断线）一律以每轮用户消息中【本周期风险预算】小节的实时推导值为准，严禁引用或臆想任何固定绝对金额。

`{{risk_budget}}` 由 `scripts/ai_brain_trader.py:620-721` 的 `build_risk_budget_text()` 生成，直接读 `scripts/risk_constants.py` 的 22 个常量（`:635` 动态 import 以支持热重载），逐行披露杠杆区间、R:R 硬底线、置信度标定带、分批止盈开关、冷静期。`:623-626` 的注释坦白了修复前的 bug：模型曾看到虚高 2.49× 的单标的额度，因为提示词自己算比例而漏了绝对封顶。

**止损倍数**：prompt 写 `1.8x~2.2x 1H ATR`（`trading_system[1]` 铁律 1），而 `scripts/ai_brain_trader.py:334-353` 的 `_sl_atr_mult_for()` 从标的池取 per-instrument 值（tier_1=1.8 / tier_2=2.2），注释 `:335` 写"提示词里展示的止损基准＝执行层真正会用的那个数"。**这一项 prompt 与代码同源。**

### 类别 4："不交易"的指令——prompt 在系统性地**反 WAIT**

这是最关键的发现，与 owner 假设的"prompt 鼓励 WAIT"**相反**。prompt 用了至少 6 处强力措辞逼模型开单：

> `trading_system[0]`：3. 拒绝懈怠与恐惧：…**必须果断**在候选标的池中选优输出限价进场指令，不得无故放弃合规机会——**空仓不是风控，无优势硬开才是风险**；日内波动活跃，只要具备顺势回踩确认、反弹承压或动能初现，**必须敏锐捕获，拒绝无为懈怠！**
> `trading_system[2]`：不得把"稳健"解释为长期空仓，更不得被解释成"只有完美共振才允许交易"…P2/P3 的轻微分歧应通过减小保证金处理，**绝不能机械全盘 WAIT**。
> `trading_system[4]`：3. 选优开单纪律：…**不得以"再等等完美共振"为由放弃合法机会**。
> `custom-tu-style`：2. 拒绝机械空仓与死等大单边…大盘横盘低波时，**全面下沉激活 15M/1H 敏捷微结构**拉升与破位破局，**严禁以"4H无趋势"为由全盘放弃机会**
> `custom-tu-style`：6. 置信度标定：形态达标且空间充足果断给出 **76%~88%**，**确保穿透执行层门禁进场**；**仅当全部候选触发硬否决时才全体 WAIT**。

WAIT 只在 4 处被允许：全部候选触发硬否决、肥尾过大、箱体半山腰、资金不匹配最小下单量。

**所以 R20 的"默认不交易"完全不来自 prompt——它只来自四道 fail-closed 拦截器。** prompt 与执行层在这里是**对抗关系**：prompt 拼命推开仓，代码拼命拦。这解释了为什么它的台账笔数不高（拦截器赢了）但方向质量差（prompt 迫使模型在没有优势时也要输出一个方向）。

**`确保穿透执行层门禁进场` 这句话本身是 prompt 在教模型如何规避风控**——它不是让模型评估真实置信度，而是让模型报一个能过门禁的数字。这是 confidence 字段被彻底污染的根因。

### 类别 5：市场环境描述——取值来源已坐实，但 active profile 丢了一个插槽

`{{market_regime}}` 的值来自 `scripts/brain/prompt.py:326-334` 调 `scripts/calculus_engine.py` 的 `detect_macro_market_regime(packages)`，取 `regime_data["summary_text"]`。

**发现**：`stable`（active）的 `trading_user` pipeline **没有** `base-ts-regime` 模块，`wide_oscillation` **有**（实测：`stable has base-ts-regime: False` / `wide_oscillation: True`）。代码预设里是有这个模块的（`scripts/prompt_library.py:346-347`）。

但 regime 文本仍然到得了模型——`scripts/brain/prompt.py:346` 把它拼进了 `market_matrix`：
```python
"market_matrix": f"{regime_text}\n\n{all_market_str}" if regime_text else all_market_str,
```
**结论**：这是一个**无害的 profile 配置缺失**，不是断链。但它意味着 active profile 与它自己的编辑器预设不同步，且 `market_regime` 变量在 active profile 里是**死变量**（有值、有渲染能力、无消费点）。

### 类别 6：输出 schema——prompt 里写了两份，且不一致

`trading_system[7]`「严格 JSON 规范契约」给了完整骨架（`decisions[instId]` 含 `action/confidence/leverage/margin_usdt/entry_price/take_profit_price/stop_loss_price/summary_reason/market_structure/calculus_dynamics/math_prob_rationale/volume_and_oi`）。

但 `scripts/brain/prompt.py:284-321` 的 user prompt **又写了一份 schema**，两份存在实质冲突：

| 字段 | system 版（`trading_system[7]`） | user 版（`brain/prompt.py`） |
|---|---|---|
| CLOSE_MARKET 触发 | `峰值回撤超 35%`（`:505`） | 无此条 |
| 峰值回撤（军规段） | `回撤超 45%~55%`（`trading_system[1]`） | — |
| leverage 示例 | 固定 `3` | `int((min_lev+max_lev)/2)` 动态插值 + "严禁无差别照抄" |

**同一份 prompt 内部，峰值回撤阈值出现 35% 与 45%~55% 两个值**（`scripts/ai_brain_trader.py:505` vs `trading_system[1]` 第 2 条）。这是纯 prompt 级的自相矛盾，代码两个都不执行。

---

## prompt vs 代码的职责边界

### LLM 输出的解析与钳制点

链路：`scripts/brain/decisions.py:assemble_decision_cache:65` → `validate()` curry → `validate_and_filter_decision:34` → `astra_backend/interceptor_manager.py:run_interceptor_pipeline:312`。

### 三列表

**A. 被代码 clamp / 覆盖的字段（4 个）**

| 字段 | 处理 | 位置 |
|---|---|---|
| `leverage` | `int(max(lev_lo, min(lev_hi, round(...))))` 双侧钳制到 `[MIN_LEVERAGE, MAX_LEVERAGE]` | `scripts/brain/decisions.py:112-114` |
| `confidence` | `max(0.0, min(100.0, ...))` 仅钳到合法区间（**不是门禁**） | `scripts/brain/decisions.py:109` |
| `margin_usdt` | 乘自进化资产乘数 `mult`，乘数本身 clamp 到 `[0.5, 1.5]` | `scripts/brain/decisions.py:119-121` |
| `suggested_sl_price` | 非 `UPDATE_SL` 时强制 `0.0` | `scripts/brain/cycle_parts.py:57-72` |

**B. 被代码硬否决（只决定 开/不开，不修改值）的 4 个维度**

| 门 | 判据 | 位置 |
|---|---|---|
| 反向持仓冲突 | 已有反向仓禁止借决策通道反开 | `astra_backend/interceptor_manager.py:342-346` |
| 价格几何 + R:R | `s<e<t`（多）/`t<e<s`（空），`rr >= MIN_RISK_REWARD_RATIO`（默认 2.0） | `scripts/order_risk.py:39-58` |
| 置信度底线 | `conf < _conf_floor_for(inst_id)` → WAIT。池内 per-instrument `conf_floor`（仅 DOGE=80），否则回落 `MIN_ENTRY_CONFIDENCE`（默认 **80.0**，`scripts/risk_constants.py:76`） | `astra_backend/interceptor_manager.py:292-309, 359-361` |
| 4H 方向 | 字符串 `in` 判断 | `plugins/interceptors/01_macro_trend_filter.py:23-26` |

**C. 代码完全不管、直接信任 LLM 的字段（决定实际盈亏的全部）**

| 字段 | 代码是否 touch | 后果 |
|---|---|---|
| **`action` 的方向选择**（BUY_LONG vs SELL_SHORT） | 仅在 4H 极端通道时否决；**4H 中性/震荡时 100% 由 LLM 定** | 方向 alpha 全在 prompt+模型 |
| **开哪个标的** | 无任何选优/排序代码 | 选股 alpha 全在 LLM |
| **`entry_price`** | 只验几何序关系，**不验是否真的在"现价外 0.02%~0.10%"** | prompt 写的微距挂单纪律**零执行** |
| **`take_profit_price`** | 只参与 R:R 计算 | 止盈位置自由 |
| `market_structure` / `calculus_dynamics` / `math_prob_rationale` / `volume_and_oi` | 原样存缓存，缺失时填默认串（`scripts/brain/decisions.py:158-161`） | prompt 要求"必须引用具体 v/a 数值"**无任何校验** |
| `macro_assessment` | 原样透传 | — |

### 结论：换一版 prompt，行为会变到什么程度？

**变化面（prompt 全权）**：开仓频率、方向分布、标的偏好、挂单距离、止盈距离、confidence 报数分布、持仓管理倾向（HOLD vs CLOSE_MARKET vs UPDATE_SL）。
**不变面（代码兜底）**：杠杆上限、保证金上限、R:R ≥ 2.0、置信度 ≥ 80（或 DOGE 80）、4H 极端方向否决、ADX ≥ 18、几何合法性。

即：**代码守住了"不会爆仓"，prompt 决定了"赚不赚钱"。** R20 的每一笔盈亏归因，扣掉爆仓保护，剩下的全部由 prompt 与模型自由裁量产生。这就是为什么 owner 的观察是对的——而且比预想更彻底。

---

## council 与自进化的 prompt 回写闭环

### council：三层验证结果

| 层 | 状态 | 证据 |
|---|---|---|
| 席位 prompt 文本 | ✅ 存在，4,427 字符 | `astra_backend/council/policy.py:11-124` |
| orchestrator 代码 | ✅ 存在且完整（提案/交叉质询/CIO 终审/变量渲染） | `astra_backend/council/debate.py:52-266`、`scripts/brain/dispatch.py:84-100` |
| 生产启用 | ❌ **默认关闭**，且配置文件不存在 | `astra_backend/council_manager.py:155` `"enabled": False`；`data/council_config.json` **本机不存在**（`ls` 报 No such file）→ 首次读取即写入 `enabled=False` 的工厂默认 |

**席位构成与对外宣传不符**。README.zh-CN.md:55 宣传"趋势、动能、数理量化、**宏观情报**"四席；README:71 宣传"资深趋势员 · 动能突破员 · 数理量化员 · CIO"。实际 `DEFAULT_PRESET_TEMPLATES` 只有 **4 个 key**：`trader_trend`(w=0.35) / `trader_momentum`(w=0.35) / `trader_quant`(w=0.30) / `cio`(仲裁, w=1.0)。**没有"宏观情报"席，没有"风控"席，没有独立"反方"席**——交叉质询是让同样这 3 个交易员互相质询（`astra_backend/council/debate.py:194` `run_seat_critique`），不是独立反方席位。context 提到的"宏观/技术/风控/反方 + CIO"这个设计**在代码里不存在**。

`COUNCIL_PRESET_SUITES:136-143` 只有 1 个套件 `hedge_fund_desk`，而 README:143 宣传"支持标准、交叉质询与**对抗辩论**三种共识模式"——`VALID_CONSENSUS_MODES:6` 实测只有 **2 个**：`{"standard", "cross_examination"}`。**"对抗辩论"模式不存在。**

席位 prompt 本身还存在与主 prompt 的**阈值漂移**：`astra_backend/council/policy.py:23` 写 `挂支撑/阻力附近（现价下方/上方 0.1%~0.6%）`，而 `6188055a` 已把主 prompt 改成 `0.02%~0.10%`。席位 prompt 停留在旧口径，**两次提频 commit 都没同步它**（`git diff --name-only` 两个 commit 均不含 `council/policy.py`）。因为 council 没启用，这个漂移不产生实盘影响——但它证明席位 prompt 是**无人维护的展示品**。

### 自进化：三项声明逐一核实

README:210 声称"outlier rejection、anti-bias rules、7–14 day sharpness half-life"。**三项全部有真实代码**，这是 R20 少数名实相符的地方：

| 声明 | 判定 | 代码 |
|---|---|---|
| outlier rejection | ✅ 有实现 | `scripts/evolution_shield.py:161-162` `if sample_size < 2: return False, "样本量不足..."` |
| anti-bias rules | ✅ 有实现，且比宣传强 | `scripts/evolution_shield.py:96-118` 共 **11 条** `POISON_PATTERNS` + `:121-124` 动词×名词共现兜底 + `:127-131` 否定子句剥离。拦"提高杠杆""放宽保证金上限""下调置信度门槛""马丁格尔加仓""满仓梭哈""关闭拦截器"。注释 `:103-106` 记录了一次真实审计：旧词表对"把单笔杠杆从 5x 提升至 20x"**全部 PASS**，已修。 |
| 7–14 天半衰期 | ✅ 有实现 | `scripts/evolution_shield.py:224-241` `is_lesson_expired()`；`BASELINE_LESSONS:42-88` 的 `ttl_days` 实测为 14/14/14/**7**；`is_baseline=True` 的**永不过期**（`:226-227`） |

**回写粒度**：结构化，不是整段文本。`{"id","rule_text","enabled","ttl_days","sample_size","is_baseline","category","created_at","health_score","shield_status"}`（schema 由 `_validate:179-196` 强校验）。LLM 只能输出 `change_status ∈ {ADD, REVISE, INVALIDATE, NO_CHANGE}` + `ai_long_term_memory` 文本数组（`scripts/self_improvement_engine.py:352-357`），宿主负责落结构。

**注入上限**：`MAX_INJECTED_LESSONS = 8`（`scripts/evolution_shield.py:263`），`render_lessons:244-257` 基线优先、其余按时间倒序截断。注释 `:261-262` 记录了一个已修的欺骗性 UI：页面把 12 条都显示为"生效中"，实际只注入 8 条。

**写回目标不是 prompt_library.json**。闭环是：台账 → LLM 复盘 → `data/ai_trading_memory.json`（结构化心法）→ 渲染成 `{{trading_memory}}` 插槽 → 注入主脑 user prompt。`prompt_library.json` 的 `revisions` **只记录人工编辑**（6 条全部是开发流水，末条 2026-09-14）。自进化**碰不到 profile 文本**——这是正确的隔离设计。

### 这个闭环是学习还是噪声放大器？

**判断：结构上防住了最坏情况，但激励上是噪声放大器。**

防住的部分（真实）：`sample_size < 2` 拒写、TTL 7~14 天自动失效、11 条毒性模式 + 共现兜底、基线心法不可物理删除（`scripts/self_improvement_engine.py:350`，宿主会把被省略的基线原样补回）、注入上限 8 条、证据纪律要求 `snapshot_observability` 分级（`:346`，`PRICE_ONLY/NONE` 一律禁止数理归因）。

仍是噪声放大器的原因（三条，均为结构性）：

1. **样本量不是统计量**。`sample_size >= 2` 是唯一的统计门。2 笔交易在加密永续上不能区分 alpha 与噪声。台账总量 104 笔、分 10 个标的 × 2 方向 = 20 个格子，平均 5 笔/格——**任何分格结论都在噪声里**。
2. **TTL 7~14 天 = 强制过拟合最近行情**。心法半衰期比市场 regime 切换周期还短，机制上保证记忆永远只反映最近两周。这不是防过拟合，这**是**过拟合，只是自动遗忘。
3. **反馈的是结果不是决策质量**。`load_closed_trades:248-340` 喂给 LLM 的是 `net_pnl / fee / exit_reason + entry_snapshot`。SUI 单币 +300.01 会让模型学到"SUI 好做"，而这是 104 笔里的单点运气。`asset_multipliers.json` 更把这个直接接回仓位（`scripts/brain/decisions.py:89-121`，乘数 clamp `[0.5,1.5]`）——**结果偏差直接变成下注偏差**，clamp 只限制了幅度，没有质疑方向。

---

## alpha 到底住在哪里（prompt / 代码 / 标的池的分成判断）

**我的判断：prompt ≈ 55%，标的池 ≈ 30%，代码 ≈ 15%。** 依据如下。

**prompt ≈ 55%**——它独占了全部**收益侧**自由度。方向、选标的、`entry_price`、`take_profit_price` 四个直接决定单笔盈亏的量，代码一个都不复核（见职责边界 C 表）。且 v8.4.0 的两次提频**实证**了这一点：`git diff --name-only 1da3ee63~1..6188055a` 对 `scripts/backtest_engine.py` 与 `scripts/factors/` 输出**为空**——两次改变交易行为的 commit，**零信号代码改动、零回测代码改动**，全靠改 prompt 词句 + 调 `risk_config.py` 常量。commit body 自述 `maximize broker commissions`。这是"prompt 即策略"的最强证据。

**标的池 ≈ 30%**——`scripts/instrument_pool.py:57-67` 的 10 个手挑主流永续是**人工 alpha 的沉淀**，而且是唯一一处**不由 LLM 决定**的选择。它的作用是**限制错误空间**：模型无法去交易流动性陷阱币种。台账证据反向印证其决定性——SUI 单币 +300.01，剔除后其余 80 笔净 −99.11；BTC+ETH+SOL 29 笔净 −181.32。**同一套 prompt、同一套代码，换标的就从 +149% 变成 −99**。这说明它的正收益不是策略产生的，是**标的池里恰好有一个大涨的币 + prompt 的高频下注恰好在它上面下了足够多注**。这是 beta 与运气，不是 alpha。

**代码 ≈ 15%**——四道拦截器 117 行只做**否决**，不做选择。它们贡献的是"没有爆仓"，即**降低方差而非提高期望**。`03_adx_volatility_filter.py` 的 `ADX < 18` 是唯一有正期望理由的门（避开震荡磨损），但它同时也是与 prompt 的 `ADX ≥ 14` 直接冲突的那道门。

**还有一部分 alpha 谁都没占：`is_live: false`。** 它的公开节点自述模拟盘。上述 +200.90 USDT 没有真实滑点、没有真实 Maker 未成交（prompt 里 `0.02%~0.10%` 微距挂单在真实盘口会大量踏空或被逆向选择），也没有资金费率的真实累计。**prompt 里那套"极速微距挂单必然 1~3 根 K 线成交"的假设，只在模拟撮合里成立。**

---

## 对 Aletheia 的可提取项与不适用项

Aletheia 是全链确定性打分（`universe/continuous_scoring.py` 六族 PCHIP → Ranker → Operator → Risk），**没有 LLM 逐标的判断环节**。以下逐条判定。

### ✅ 可提取项 1：把"方向门"编译成 Ranker 前置过滤器

- **解决什么问题**：R20 的 `01_macro_trend_filter` 是它四道门里唯一有明确经济含义的一道（大级别趋势与开仓方向冲突时不开）。它**完全不需要 LLM**——`"4H_MACRO_BULL" in macro_4h` 是纯字符串判断。
- **无 LLM 层如何落地**：在 Ranker 之前加一个方向一致性 mask：对每个标的算高周期趋势状态（Aletheia 已有连续打分基础设施，可直接取某一族曲线的长周期分量符号），与候选方向不一致的候选**在进入 Ranker 排序前剔除**，而不是靠打分权重软惩罚。理由：R20 的 fail-closed 语义是"否决"，而打分体系里"低分"仍可能因其他族补偿而进入前列。
- **代价**：在趋势状态翻转的边界附近会丢掉真实的反转机会；需要一个滞回带（hysteresis）否则会在阈值附近抖动。Aletheia 的 PCHIP 曲线本身是连续的，做成硬 mask 会引入不连续点——建议用**宽死区**而非单点阈值。

### ✅ 可提取项 2：`{{risk_budget}}` 的"单一事实源披露"模式 → 决策日志

- **解决什么问题**：R20 `build_risk_budget_text()`（`scripts/ai_brain_trader.py:620-721`）的真正价值不在"告诉模型"，而在**它强迫风控口径只有一处定义**。`:623-626` 的注释记录了口径分裂的实际后果：模型按虚高 2.49× 的额度规划，执行层按 `min()` 夹掉。
- **无 LLM 层如何落地**：Aletheia 无需向任何模型"披露"，但可以让 Risk 层在每次决策时**输出一份同源的生效参数快照**进决策日志（杠杆上限、单笔风险、R:R 底线、当前熔断余量），由同一组常量对象生成。价值：任何"为什么这单是这个size"的事后追问都有确定答案，且参数分裂会立刻可见。
- **代价**：多一条日志写入；需要确保快照与实际执行读的是同一对象而非副本（R20 用 `:635` 调用期 `import` 解决热重载，Aletheia 若参数是不可变配置则更简单）。

### ✅ 可提取项 3：结构化心法的 TTL + 毒性词表 → 参数漂移护栏

- **解决什么问题**：`scripts/evolution_shield.py:96-124` 的 11 条 `POISON_PATTERNS` + 共现兜底，本质是**"任何自动化回路不得放宽自身风控约束"**这条不变量的可执行形式。这个不变量与 LLM 无关。
- **无 LLM 层如何落地**：Aletheia 若有任何自动调参/自适应逻辑（打分权重自调、阈值自适应），给它加一道**单向阀**：自动回路只能收紧风险参数，放宽必须走人工路径。实现成一个断言式守卫，不是正则匹配文本——Aletheia 是数值参数，直接比大小即可，**比 R20 的正则方案更可靠**。
- **代价**：几乎为零；但需要先明确哪些参数属于"风险方向"（哪边是收紧）。

### ✅ 可提取项 4（反向教训，不是模仿）：把"反 WAIT"倾向当作已知反模式检查

- **解决什么问题**：R20 最大的自毁是 prompt 用 6 处措辞逼模型开单，包括 `确保穿透执行层门禁进场`。Aletheia 没有 prompt，但**等价物存在**：任何让"必须产出一个候选"成为隐含约束的设计——例如 Ranker 总是返回 top-N、Operator 总是从非空候选集里选一个。
- **无 LLM 层如何落地**：显式检查 Aletheia 的链路里是否存在"空集不被允许"的路径。确定性打分的正确行为是：**当所有标的的分数都低于绝对门槛时，返回空集**，而不是返回"相对最好的那个"。相对排序 + 固定 top-N = 结构性的反 WAIT。
- **代价**：需要一个绝对分数门槛，而绝对门槛比相对排序难校准。但这正是 R20 亏在做空侧的同类问题：被迫在无优势时选一个方向。

### ❌ 不适用项 1：prompt profile 的多方案热切换

`active_profile_id` 切换整套交易风格（`stable` ↔ `wide_oscillation`）是自然语言系统独有的廉价可变性。Aletheia 的等价物是换一组打分曲线参数，**但那需要回测支撑**——而 R20 恰恰是**在零回测验证下**切换的（两次提频 commit 零回测代码改动）。**这个"优势"是它最大的风险源，不要移植。**

### ❌ 不适用项 2：四大王牌入场形态

`trading_system[4]` 的四个形态（Pullback to Value / Throwback to Resistance / Liquidity Sweep & Reclaim / Sweep & Fail）在 R20 里**代码零实现**，纯靠 LLM 视觉式识别自然语言描述。要在 Aletheia 落地必须先把每个形态写成确定性检测器（扫单方向、收回幅度、时间窗、量能确认），那是**四个独立的因子工程项目**，与"抄 prompt"完全不是一回事。**当前标注不适用**；若将来要做，它们是四个候选因子，不是四条规则。

### ❌ 不适用项 3：席位辩论 / 多模型交叉质询

需要 N 个独立 LLM 判断再聚合。Aletheia 无 LLM 判断层，且 R20 自己**默认关闭**（`astra_backend/council_manager.py:155`）、配置文件不存在、席位 prompt 阈值已过期未同步。**零参考价值。**

### ❌ 不适用项 4：`confidence` 字段本身

R20 的 confidence 是被 prompt 明确指示要"报够 76%~88% 以穿透门禁"的数字，**不是概率**。Aletheia 若需要置信度概念，必须来自打分分布的统计性质（如分数与历史实现收益的校准曲线），而不是任何自报值。R20 在这一项上提供的是**纯负面示范**。

---

## 幻象项

按"写了/宣传了 vs 代码实际"分类。

### A. prompt 写了、代码不执行

| 幻象 | prompt 出处 | 代码实况 |
|---|---|---|
| **微距 Maker 挂单纪律 `0.02%~0.10%`** | `custom-tu-style` 第 3 条、`trading_system[5]` | **零校验**。`scripts/order_risk.py:39-48` 只验 `s<e<t` 序关系，从不比较 `entry_price` 与现价的距离。模型可以报任意 entry 只要序关系对 |
| **`P续 ≥ 46%~50%` 概率定价** | `trading_system[3]` | 无任何代码读取模型是否按此决策 |
| **四大王牌入场形态** | `trading_system[4]` | 无形态检测代码 |
| **三阶利润棘轮的模型侧** | `trading_system[1]` 第 2 条 | 代码有**自己独立的**棘轮（`scripts/trader/protection.py:91-115` `ratcheted_trailing_stop`，tier1=1.5x ATR 保本 / tier2=2.2x ATR 锁 1.0x ATR），与 prompt 描述的 R 倍数口径**不同**（prompt 说 1.5R/2.2R，代码用 ATR 倍数）。模型输出的 `UPDATE_SL` 还要过 `ai_tightens_stop:53-88` 三重检查（浮盈≥1.2x ATR、新 SL 落在 [入场,现价] 内、留 0.7x ATR 缓冲）——**prompt 的棘轮档位与代码的棘轮档位是两套独立机制** |
| **`calculus_dynamics` 必须引用具体 v/a 数值** | `trading_system[3]` 结尾"严禁只写空泛定性词句！" | `scripts/brain/decisions.py:159` 缺失就填 `"模型未提供具体微积分证据"`，**照常开单** |
| **`ADX ≥ 14 即可参与`** | `trading_system[2]` P2 | `plugins/interceptors/03_adx_volatility_filter.py:23` 拦 `< 18`。prompt 比代码松 4 个点，模型按 14 出的单会被拦 |
| **峰值回撤 35% vs 45%~55%** | `scripts/ai_brain_trader.py:505` 写 35%；`trading_system[1]` 写 45%~55% | 两者都不执行。同一份 prompt 内部矛盾 |

### B. 对外宣传、默认关闭或不存在

| 宣传 | 出处 | 实况 |
|---|---|---|
| 多模型投委会 | README.zh-CN.md:55,142-145,207 | `astra_backend/council_manager.py:155` `enabled=False`；`data/council_config.json` **不存在** |
| "宏观情报"席位 | README.zh-CN.md:55 | `astra_backend/council/policy.py:11-124` 只有 trend/momentum/quant/cio 四席，**无宏观席、无风控席、无独立反方席** |
| "标准、交叉质询、**对抗辩论**三种共识模式" | README.zh-CN.md:143 | `astra_backend/council/policy.py:6` `VALID_CONSENSUS_MODES` 实测只有 2 个 |
| 每 6 小时自进化在跑 | README.zh-CN.md:101,209 | 代码路径完整，但 `data/ai_trading_memory.json` **本机不存在**，`revisions` 末条停在 2026-09-14 且 6 条全是人工开发流水。**本仓库快照内无任何自进化实际运行产物** |
| `is_live` | 公开节点 API | 自述 `false`（模拟盘）。全部台账数字无真实滑点与真实 Maker 成交率 |

### C. 配置级幻象

| 项 | 实况 |
|---|---|
| `wide_oscillation` profile | 存在、`enabled=True`，但 `active_profile_id="stable"`。**从未投产**，且它是 `1da3ee63`（提频 commit）新建的 |
| `{{market_regime}}` 在 active profile | `stable` 无 `base-ts-regime` 模块 → 该变量在 active profile 里是死变量（值仍经 `market_matrix` 到达模型，`scripts/brain/prompt.py:346`） |
| `revisions` 占 40% 文件体积 | 115,055 / 285,077 bytes 是 6 条历史快照，其中 `rev-c4b70f1a6dc9` 的 note 自述是**已知有缺陷的中间态**（"渲染会 base+legacy 翻倍"） |
| 置信度标定带下沿硬编码 78 | `astra_backend/risk_config.py:400-401` / `scripts/ai_brain_trader.py:697` 用 `max(MIN_ENTRY_CONFIDENCE, 78.0)`。aggressive 套件把门禁降到 68（`astra_backend/risk_config.py:201`）时，模型看到的标定带仍是 78%~86%——**"降门禁抢先上车"的宣称与模型实际收到的指导值不一致** |
| `02_confidence_gatekeeper` 的 `context.min_confidence` | `astra_backend/interceptor_manager.py:340-341` 只放 `active_inst_ids` 与 `active_position_sides` 进 context（`grep -rn "min_confidence"` 全仓确认无任何生产代码注入该键）→ 插件 `:29` **永远走 75.0 兜底**，与全局 `MIN_ENTRY_CONFIDENCE=80` 不一致。实际不产生放松，因为 `:359-361` 的核心门已先用 80 拦过——**这道插件在默认配置下是死代码** |

---

## 本次未验证

- **未运行任何代码**。`python3` 仅用于 `json`/`ast` 静态解析；未 import 项目模块（`scripts/market_data_service.py:17` 需要 `requests`，本机缺失，按契约不装依赖）。故 `apply_module_layout` 的实际拼装输出**未实跑验证**；base↔JSON 一致性是我用 `ast` + 标题切分**复现其切分逻辑**得出的 8/8 相等，不是调用 `base_template_modules()` 的实测。
- **未跑任何测试**（`tests/llm/` 与 `tests/core/` 下有 40+ 个 prompt 相关测试文件，未执行）。`evolution_shield` 三项声明的判定基于读代码，未跑 `tests/llm/test_evolution_shield_gates.py` 验证。
- **无运行期证据**。`data/` 目录下只有 `prompt_library.json` 与 25 个 `tmp*` 文件；`ai_trading_memory.json`、`council_config.json`、`ai_brain_history.json`、交易台账均不在仓库快照内。故"自进化是否真在每 6 小时跑""council 是否曾被手动开启过"**无法从本仓库证实或否证**——只能确认默认关闭且无产物留痕。
- **token 数为估算**：按中文 1 字符 ≈ 1 token 粗估，未用任何 tokenizer 实测。字符数是精确的。
- **`.env` 实际生效值未知**。所有风控数字取自 `risk_constants.py` / `risk_config.py` 的**代码默认值**（`MIN_ENTRY_CONFIDENCE=80.0`、`MAX_LEVERAGE=5.0`、`MIN_RISK_REWARD=2.0` 等）。生产 `.env` 不在仓库，若已应用 aggressive 套件则实际为 68 / 9.9 / 2.0。
- **公开台账与 `is_live: false` 未本次复核**，直接采用 context 提供的既有事实。
- **alpha 三分成（55/30/15）是我的判断**，非可测量分解。依据是自由度归属（收益侧自由度全在 prompt）+ 两次提频 commit 的零信号代码改动实证 + SUI 剔除前后的台账反差。未做归因回归。
