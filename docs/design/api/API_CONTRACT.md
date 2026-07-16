# API Contract

> 新增/修改端点时必读。完整端点索引见 [`docs/design/api/api_reference.md`](api_reference.md)。

---

## 基础约定

| 项目 | 规则 |
|------|------|
| Base URL | `http://localhost:8787/api/`（frontdoor 代理）/ 直连后端 `:8788` |
| Auth | 无（本地网络专用；Cloudflare Tunnel 另行处理）|
| 错误格式 | `{"detail": "message"}` + 标准 HTTP 状态码 |
| 时间戳 | ISO 8601 UTC（`+00:00` 格式，**非** `Z`，见前端解析陷阱）|
| 分页 | `?limit=N&offset=M` |

### HTTP 状态码语义

| 码 | 含义 |
|----|------|
| 200 | OK |
| 400 | 参数错误 |
| 403 | 被权限模式/安全门控拦截（如无 unlock 的实盘操作）|
| 404 | 实体不存在 |
| 409 | 状态冲突（如重复 unlock、审批已执行的 proposal）|
| 422 | Pydantic 校验失败 |
| 503 | 后端依赖不可用（DB/scheduler/exchange/model）|

---

## 兼容性规则

新增端点前必须：

1. **先在 `docs/design/api/api_reference.md` 登记**（SDD Rule §6），再写路由代码。
2. **不得改变已有端点的 response schema**，只能新增字段（向后兼容）。
3. **async def handler 必须满足性能要求**（见 AGENTS.md API 性能规则节）：
   - 热路径读加 TTL 缓存（5-60s）
   - 冷路径 <100ms（直连 :8788 实测）
4. **信任边界端点**必须写 audit 事件（见下表）。

---

## 信任边界端点（额外要求）

| 端点 | 强制要求 |
|------|---------|
| `POST /settings/live-unlock` | `confirm=true` + 写 audit |
| `POST /risk/kill-switch` | 仅人工操作 + 写 audit |
| `POST /settings/exchange-credentials` | 只写不读（GET 不得返回明文密钥）|
| `POST /proposals/{id}/approve-paper` | 保留原始 proposal ID，不得创建重复行 |

---

## Symbol 格式规则

- **API 请求/响应体**：使用 canonical 格式（`BTC/USDT`，带斜杠）
- **执行适配器边界**：通过 `to_bitget()` 转换为 `BTCUSDT`
- **前端显示**：永远走 `fmtSymbol(symbol, marketType)`，perp 去斜杠，spot 保留

---

## 响应 schema 权威来源

Response body 字段定义在 **代码的 Pydantic model** 里，不在 api_reference.md 里。
`docs/design/api/api_reference.md` 只是端点索引，字段级真相以代码为准（防文档漂移）。

---

## 深度阅读

- 完整端点索引（~293 个）→ [`docs/design/api/api_reference.md`](api_reference.md)
- 数据模型（Pydantic models + schema）→ [`docs/design/data/data_model.md`](../data/data_model.md)
- 信任边界契约 → [`docs/governance/policies/trust_and_change_control.md`](../../governance/policies/trust_and_change_control.md)
