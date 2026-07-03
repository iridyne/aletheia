# 选币池方向偏斜诊断（2026-07-03）

> 来源：owner 观察「选出的标的理由几乎清一色 momentum，mean_reversion / short_bias /
> volatility 极少出现，怀疑自进化断裂」。本文用生产库 `data/app.db` 的真实数据把
> 这个模糊观察落成可量化的根因，并说明已修 / 待修。

## 1. 数据确认偏斜（真实且严重）

最近 200 条 opportunities：

| family | 数量 | 占比 |
|---|---|---|
| momentum | 168 | 84% |
| mean_reversion | 22 | 11% |
| volatility | 6 | 3% |
| short_bias | 4 | 2% |
| derivatives / event | 0 | 0% |

方向：long 170（85%）、neutral 29、short 1（0.5%）。

member 层（allocate 之后、emit opportunity 之前，最近 500）：momentum 81% / MR 12% /
vol 4.6% / short 1.4%。**偏斜在 member 层就已存在**——不是 emit 环节造成的。

## 2. 逐 family 打分分析（`all_scores`，UP-14 已持久化，500 条 member）

| family | 触发率(score>0) | 胜率(该 symbol 最高分) | 均分 |
|---|---|---|---|
| momentum | 59% | 43% | 22.2 |
| mean_reversion | 20% | 12% | 5.7 |
| volatility | 19% | 3% | 6.1 |
| **derivatives** | **0%** | **0%** | **0** |
| event | 1% | 0% | 0.8 |
| short_bias | 6% | 1% | 2.1 |

## 3. 三层根因（按可修性排序）

### RC1 — derivatives / event 两个 family 事实上是死的（数据源缺口）
- derivatives 分数**恒 0**（funding_rate / OI 数据没喂进 scorer，与
  `data-source-audit-2026-06-28` 记录的 OI 历史被 Bitget 吞、清算数据为 0 同源）。
- event 触发率 1%，近乎不产出。
- 但**静态 family_quota 默认给了它们 40%**（derivatives 0.35 + event 0.05）。这份
  份额永远填不满，被 momentum 通过 explore 回填 → 直接抬高 momentum 占比。

### RC2 — explore 阶段绕过 family 配额，把 momentum 又灌回去
- `allocate_universe` 的 exploit 阶段**正确**地把 momentum 封在配额上限；但 explore
  阶段从 remainder 随机补样，而 remainder 几乎全是「超配额被踢出」的 momentum →
  explore 把刚封顶的 momentum 又塞回来，悄悄抵消了配额平衡。
- 这是 84% momentum（配额只有 30%）的直接机制。

### RC3 — regime 检测 75% 出 None → regime 感知逻辑休眠
- 最近 200 条 opportunities 的 regime_tag：150 条 None、50 条 sideways。
- regime=None 时用**静态** family_quota（即 RC1 那份 40% 给死 family 的配额）；
  regime-aware 配额（bull/bear/sideways/volatile）和 `scanner_regime_scoring` 方向
  加权基本没机会生效。
- 另：winner-take-all（`run_all_scanners` 每 symbol 只 emit 最高分 family）+
  momentum 触发率最高（59%），本身就让 momentum 天然占多数。多发次级信号的路子被
  `service.py:216-222` 的「按 symbol 去重留最高分」堵死（且违反每 symbol 单
  instrument 不变量），不走。

## 4. 本次已修（W5，安全 + 数据驱动，不做低维硬拦截）

- **RC2 修复**（`allocator.py`）：explore 阶段只从「仍在配额内」的 family 取样，不再
  回流已超配额的 winner family。锁定 spec **UP-17**（行为断言，RED→GREEN）。
- **RC1 缓解**（`config/schema.py` 静态 family_quota 默认重平衡）：momentum 0.35→0.30、
  derivatives 0.35→0.10、event 0.05 保留；volatility 0.20 / short_bias 0.10 给出显式
  份额（此前靠 0.3 默认上限）。sum=1.0。运行态 `data/config.yaml` 同步（不入 git）。

**量化预期**：合成池（81% momentum 输入、regime=None）下，选中集 momentum 占比从
~84% 降到配额上限附近（≈30–50%，视非 momentum 候选供给），mean_reversion / volatility /
short_bias 拿回被 momentum 抢走的名额。

## 5. 待修（超出本分支，需单独工作 + owner 决策）

- **RC1 根治**：修 derivatives（funding/OI 喂数）+ event 数据源，让这两个 family 真正
  产出信号——属数据源工程，与 `data-source-audit-2026-06-28` 的免费填洞方案合并做。
- **RC3 根治**：regime 分类器 75% 出 None 的问题——查 regime detector 为何常不分类；
  修好后 regime-aware 配额 + 方向评分才会真正接管。
- 二者都不做「强行塞多样性」，遵守 owner 定调的高维可推翻自进化、拒绝低维硬拦截
  （见记忆 `negfeedback-high-dimensional-evolve`）。
