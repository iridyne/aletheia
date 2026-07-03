# Universe Post-Fix Distribution Audit 2026-07-03

来源: `discussion.md` D03, `docs/_ai/DISCUSSION_REMAINING_PLAN_2026-07-03.md` P1-2。

边界: 本次只做 Universe 分布复测。测试使用隔离 SQLite 临时库; 生产 `data/app.db` 只读采样。
未写生产 DB, 未改风险、执行、live、凭证或下单路径。

## 1. 结论

Universe skew 不能仅凭生产库当前分布宣称已修, 因为集成分支尚未部署到运行中的主库。
当前生产库仍保留旧分布; 但在 `integration/discussion-2026-07-03` 代码上, 一个真实
`UniverseService.refresh()` 写库路径已经能同时证明四项修复:

1. family 分布不再被 momentum 单一占满。
2. 同 symbol spot/perp 去重会在 `preferred_market_type="auto"` 下保留 perp。
3. `regime_tag` 会写入 `universe_members`。
4. perp opportunity 会携带 `funding_rate/open_interest/oi_delta_1h`。

## 2. 当前生产库基线（只读）

采样 DB: `C:\Users\ollama\Projects\aletheia-trading-os\data\app.db`

| 指标 | 当前值 |
|---|---:|
| DB size | 448,491,520 bytes |
| latest universe snapshot | `7aba6911f1f6` |
| latest snapshot summary | scanned 375 / filtered 25 / selected 14 |
| last 500 members, momentum | 408 / 500 |
| last 500 members, mean_reversion | 62 / 500 |
| last 500 members, volatility | 23 / 500 |
| last 500 members, short_bias | 7 / 500 |
| last 500 members, spot | 500 / 500 |
| last 500 members, perp | 0 / 500 |
| last 500 members, `regime_tag` NULL | 500 / 500 |
| last 200 crypto opportunities, momentum | 168 / 200 |
| last 200 crypto opportunities, mean_reversion | 22 / 200 |
| last 200 crypto opportunities, volatility | 6 / 200 |
| last 200 crypto opportunities, short_bias | 4 / 200 |
| last 200 universe_scanner opps with funding/OI/OI_delta | 113 / 200 |

Interpretation: 生产库仍是 pre-integration 状态, 不能用它证明 post-fix runtime 分布。

## 3. 隔离 post-fix refresh 结果

新增测试: `tests/specs/spec_universe_distribution_audit.py::test_universe_refresh_post_fix_distribution`

测试方法:

1. 在 `tmp_path` 新建 SQLite 并跑 migrations。
2. fake registry 插入 7 个 raw instruments: `BTC spot`, `BTC perp`, 以及 5 个不同 family。
3. fake features/scanners 固定各 family 分数, 其中 `BTC spot` 分数高于 `BTC perp`。
4. fake regime 固定 `sideways`, `long_bias=0.8`, `short_bias=0.8`。
5. 调用真实 `UniverseService.refresh()`。
6. 从 DB 读取 `universe_members` 和 `opportunities` 做分布断言。

精确断言:

| 指标 | 期望 / 实际 |
|---|---:|
| raw instruments | 7 |
| selected members after same-symbol dedupe | 6 |
| momentum members | 1 / 6 |
| mean_reversion members | 1 / 6 |
| volatility members | 1 / 6 |
| derivatives members | 1 / 6 |
| event members | 1 / 6 |
| short_bias members | 1 / 6 |
| BTC selected market | `perp` |
| members with `regime_tag="sideways"` | 6 / 6 |
| deterministic opportunities emitted | 6 |
| perp opportunities with funding/OI/OI_delta | at least 1, proven non-null |

## 4. Verification

Narrow:

```powershell
uv run pytest tests/specs/spec_universe_distribution_audit.py::test_universe_refresh_post_fix_distribution -q
```

Result: `1 passed in 0.68s`.

Broader:

```powershell
uv run pytest tests/specs/spec_universe_distribution_audit.py tests/specs/spec_universe_pipeline.py tests/specs/spec_market_type_dedup.py tests/test_universe_signal_emitter.py -q
```

Result: `67 passed in 3.43s`.

## 5. Status Update

| Item | Previous status | New status |
|---|---|---|
| D03 Universe skew | partially fixed, post-fix distribution unverified | isolated refresh distribution proven |
| Production runtime distribution | stale/pre-integration | still needs deployment + fresh refresh |
| Remaining D03 risk | event family source quality + real post-deploy sample | track after merge/deploy |

Next required proof after deployment:

```text
Run one real Universe refresh with integration/discussion-2026-07-03 deployed,
then re-sample latest 500 universe_members:
- momentum share
- market_type spot/perp split
- regime_tag NULL rate
- derivatives/event member count
- opportunity funding/OI/OI_delta coverage
```
