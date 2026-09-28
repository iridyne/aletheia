# 08 · 前后端剥离：前端重写前可先做的后端修复

> 日期：2026-09-28
> 背景：**Owner 决定前端页面整体重写**，因此把 07 域的性能缺陷按"改前端 / 改后端"剥离，只有后端部分现在实施。
> 基线：`master@6525cee5a` · gateway `http://10.2.2.66:8787`
> **性质：分析与汇报，不含代码改动。**

---

## 1. 剥离原则

一条缺陷归入"后端先修"，必须同时满足：

1. **修复点在服务端代码**，不需要改任何前端文件；
2. **新前端天然受益**，不需要知道这件事、不需要传新参数；
3. 它是**契约层面的错误**，而不是调用方式的错误——即无论前端怎么重写，当前行为都是错的。

反之，凡是"服务端能力已就绪、只是调用方没用对"的，归前端，**随重写处理**。

---

## 2. 剥离结果总表

| 原 issue | 后端部分（现在做） | 前端部分（随重写） | 剥离依据 |
|---|---|---|---|
| #3624 OHLCV 缓存 | **#3624 本身即后端专属** | 无 | 错在服务端缓存键 `limit == 100`；前端请求 300 是合理的 |
| #3625 positions payload | **#3631** 裁 blob 列投影 | #3632 调用方传 `?status=open` | `?status=open` 服务端**早已可用**，所以 scope 归前端；blob 列投影是服务端契约错误 |
| #3626 重复请求 | 无 | #3626 本身即前端专属 | 成因是 query key 失效扇出 |
| #3628 SSE 扇出 | 无 | #3628 本身即前端专属 | 成因在 `CommandCenter.tsx:490-511`；三个 operator 端点本身健康（实测 44KB/13 列，无投影问题） |

### 现在就能做的只有两条

| # | issue | 改哪里 | 收益 | 前端需配合？ |
|---|---|---|---|---|
| 1 | **#3624** | `core/aletheia/server/routes/data.py:1673` | K 线 **2400ms → 数十 ms**（R20 同形状实测 65ms，量级可达） | **否** |
| 2 | **#3631** | `core/aletheia/server/routes/data.py:1242-1256` 投影层 | positions 列表 **3041 KB → 531 KB（−83%）** | **否** |

**这两条合计覆盖了 07 域最痛的两个症状（开图慢、主线程卡），且完全不触碰前端。**

---

## 3. 为什么 `?status=open` 归前端而不是后端

这是本次剥离中唯一需要判断的一条，结论可能反直觉。

服务端参数**早已存在**（`core/aletheia/server/routes/data.py:1242-1244`）：

```python
def get_positions(
    status: Annotated[str | None, Query(pattern="^(open|closed)$")] = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 200,
```

而且 docstring（`:1246-1251`）记录了它**就是为前端补的**：

> "`status` and `limit` are honored server-side. The route previously declared no parameters, so `?limit=`/`?status=` sent by the dashboard were dropped by FastAPI..."

实测确认可用：

```
GET /api/positions                 200 rows  3041.2 KB  0.212s
GET /api/positions?status=open       4 rows    10.9 KB  0.023s
GET /api/positions?status=open&limit=50  4 rows  10.9 KB  0.022s
```

客户端封装也支持（`frontend/src/api/client.ts:1950-1956` 已处理 `status`/`limit`），**5 个调用点全部空参调用**：

```
frontend/src/pages/Charts.tsx:702
frontend/src/pages/CommandCenter.tsx:259-262
frontend/src/pages/TradesPositions.tsx:453-454
frontend/src/components/CausalChain.tsx:200-201
frontend/src/lib/useRealtimeUpdates.ts:23-24   （经 positionKeys.all 失效）
```

**所以这不是后端缺能力，是前端没用。改默认值反而会改变现有调用方语义（一个隐蔽的破坏性变更）。** 归前端、随重写处理是正确的。

> **但要注意**：如果重写时忘记传参，这个问题会 1:1 复现。因此 #3632 的主要价值是那份**契约备忘**，而不是代码改动。

---

## 4. 真正属于后端的那部分：blob 列投影（#3631）

按列拆解 `/api/positions` 的 JSON 字节：

| 列 | 总字节 | 空值率 |
|---|---:|---|
| **`decision_reasoning`** | **1,807,035 B ≈ 全 payload 60%** | 0/200（全都有值） |
| `reconciliation_evidence` | 350,507 B | 4/200 |
| `entry_market_summary` | 120,062 B | 31/200 |
| `trailing_json` | 96,257 B | 107/200 |
| `exit_reasoning` | 83,419 B | 4/200 |
| `reconciliation_identity` | 63,053 B | 4/200 |
| `last_market_signal_evidence` | 13,606 B | 188/200 |

**只移出这 7 列：3,041 KB → 531.5 KB（−83%），纯服务端，前端一行不改。**

另有 **11 列在全部 200 行恒为 `null`**：

```
realized_pnl_unavailable_reason, strategy_id, historical_accounting_status,
historical_accounting_pnl, historical_accounting_reason, historical_accounting_method,
exchange_tpsl_order_id, thesis_invalidation_json, exit_limit_price, exit_limit_source,
sizing_manifest
```

（`exchange_tpsl_order_id` 恒 null 本身是 #3619 的证据，不是这里要修的。）

**为什么这是契约错误而非调用错误**：`decision_reasoning` 是逐仓位的决策叙述文本，列表视图不可能同时展示 200 段叙述——它属于单仓明细，而 `GET /api/positions/{id}`（`routes/persistence.py:197`）已经存在。**任何前端都不该在列表响应里收到每行 9KB 的叙述文本。**

组合效果（供参考，不作为单条 issue 的验收）：

| 口径 | 解码体积 |
|---|---:|
| 现状（全量 × 87 列） | 3,041 KB |
| 仅裁 blob 列（#3631，后端） | **531 KB** |
| 仅传 `status=open`（#3632，前端） | 10.9 KB |
| 两者都做 | **10.0 KB** |

**注意：#3631 的收益不会被 #3632 取代。** 历史视图（分页拉已平仓）仍然会命中 blob 列——只要有任何一个列表调用存在，#3631 就在省钱。

---

## 5. 为什么其余两条不值得现在动

**#3626（`/charts` 200 次调用）与 #3628（`/command-center` 241 次调用）都在将被重写的页面里。**

- #3628 的成因是 `CommandCenter.tsx:490-511` 每个 SSE 事件失效 10 个 query key——这是纯前端的缓存策略问题。
- 我额外核实了它打的三个端点**本身没有后端问题**：`operator/tasks` 50 行 / 13 列 / 44KB / 30ms、`operator/sessions` 50 行 / 14 列 / 26KB / 79ms、`operator/status` 26 列 / 1KB。**没有 #3631 那类投影缺陷，所以这里没有可剥离的后端工作。**
- 现在修等于改两遍。

**但 #3624 落地后，这两条的痛感会自动下降**：`/charts` 的 35 次 OHLCV 调用从每次 2.4s 变成数十 ms。

---

## 6. 给前端重写的契约备忘（本域对重写最大的价值）

重写时容易 1:1 复现的坑，按重要性排：

1. **`/api/positions` 默认返回全部历史，不是当前持仓。** 名字有误导性——我在分析中就据此误判过一次（把 200 行读成"200 个未平仓、仓位堆叠"，实际只有 4 个 open）。**必须显式传 `?status=open`。**
2. **"当前持仓"与"历史持仓"用不同的 query key**，否则一次失效同时触发两种昂贵读取。
3. **SSE 事件优先 `setQueryData` 从负载更新**，不要失效后重取；确需重取时按 key coalesce。`CommandCenter.tsx:495-498` 已有正确范例可抄。
4. **不要让请求量随事件量线性增长**——建议重写时就带"单路由挂载请求预算"断言。
5. **K 线请求的 `limit` 必须是服务端可缓存的值。** #3624 修好后建议同时加断言，否则重写时换个数字会静默重现该缺陷。
6. 重端点（`positions`、`universe/workbench`）的轮询频率应与 payload 重量解耦；已有 WebSocket 通道（`/api/ws/ticket`）可能替代部分轮询。

---

## 7. 实施顺序建议

```
现在（前端重写前）：
  1. #3624  OHLCV 缓存键纳入 limit           —— 后端，收益最大，风险最低
  2. #3631  positions 列表裁 blob 列投影      —— 后端，−83%
       ↓ 两条都不触碰前端，可并行

前端重写时：
  3. #3632  调用方传 status=open + 历史分页
  4. #3628  SSE 扇出改 setQueryData + coalesce
  5. #3626  收尾其余重复请求
```

**#3624 与 #3631 之间无依赖，可并行。** 两条都需要重新生成 OpenAPI 类型（#3631 会改变响应字段集），这是唯一与前端的接触面——但那是生成物，不是手写代码。

---

## 8. 本域未验证

- **#3631 是否有消费者依赖列表里的 blob 列**。只确认了 5 个 `getPositions` 调用点存在，未逐个检查它们是否读取 `decision_reasoning` 等字段。**这是实施前必须先查的一步**，否则会破坏现有页面（即使它将被重写，中间期仍要可用）。
- **11 个恒 null 列是否仍有写入方**。只确认读侧恒空。
- **`/api/positions/{id}` 是否真的返回完整宽行**。假设它是明细端点，未实测。
- 其余未测路由（`/learning`、`/news`、`/settings` 等）是否存在同类后端投影问题。
- `/api/universe/workbench`（`/universe` 路由 ×4 = 111KB）未做列级拆解，可能存在同类问题。
