# TickFlow Wave B-01 事后契约审查 — 2026-08-10

## 文档角色

这是一份针对已合并 Wave B-01 的日期化审查证据，不替代当前 Dashboard Data
Presentation Contract。审查基线为 Aletheia `origin/master` 的 `11b39bdf`。

Wave B-01 在原定契约审查门之前就已实现并合并。本文补回缺失的审查，供
owner 明确选择保留、收窄或回退现有契约主张。本文不授权继续迁移表格、改变
运行行为、创建 PR 或合并。

## 本次审查的边界

已合并切片在 `frontend/src/components/DataState.tsx` 中加入八种呈现标签，
让 `DataTable` 接受可选的 `state` 与 `stateMessage`，并在 Learning Knowledge
中形成三个显式 `DataTable` 调用点。它没有增加后端 response envelope，也
没有增加新的持久状态。

审计基线上共有十个生产 `DataTable` 调用点。Learning Knowledge 的三个分支
显式传入状态；Agent Activity、Blotter、Trades/Positions 与 Universe 继续使用
兼容行为。

## 当前源码事实

| 呈现状态 | 当前生产证据 | 审查结论 |
|---|---|---|
| `loading` | Learning Query 正在首读且没有 retained data | 有真实调用方支持 |
| `available` | Learning Query 成功且存在行 | 有真实调用方支持 |
| `empty` | Learning Query 成功且返回零行 | 有真实调用方支持 |
| `unavailable` | Learning Query 失败且没有 retained data | 有真实调用方支持 |
| `stale` | Learning Query 失败但仍有 retained data | 有真实调用方支持；旧行继续可见 |
| `partial` | helper 与呈现分支存在，但 Knowledge 调用方没有传入明确的 partial/coverage 事实 | 已实现但未接入该生产消费者 |
| `not-configured` | 没有已审计的生产 `DataTable` 调用方提供明确配置事实 | 已实现但未接线 |
| `permission-limited` | 没有已审计的生产 `DataTable` 调用方提供明确权限事实 | 已实现但未接线 |

这里的关键是生产证据。Helper 分支或组件测试可以证明代码被调用时如何表现，
但不能证明产品消费者拥有诚实调用该分支所需的事实。

## 故事一：成功的空结果与首次读取失败

Operator 打开 Learning Knowledge，所选类别确实没有记录。请求成功并返回空
数组，表格显示调用方拥有的空态文案，例如 “No lessons yet.”。

如果首次请求失败且没有 retained data，同一个页面必须改为说明数据不可用，
不能把传输或后端失败包装成一次看似成功的空结果。

已合并实现支持这个故事。Loading 与 error 来自 TanStack Query；只有成功读取
后的零行才会变成 `empty`。

## 故事二：刷新失败但仍有上次成功数据

Operator 正在查看已成功读取的 Knowledge 行，此时刷新失败。删除旧行会隐藏
最后一次已知结果；不加说明地继续展示又会暗示它仍然新鲜。因此页面显示 stale
警告，同时保留旧行。

已合并实现支持这个故事。`learningReadState()` 把“发生错误且有 retained data”
映射为 `stale`，`DataTable` 同时渲染警告和旧行。WebSocket 的连接状态本身不能
产生这个状态。

## 故事三：partial、配置与权限主张

假设某个 endpoint 只返回请求的 100 条中的 78 条，明确报告某个 section 失败，
或者明确表示 capability 未配置或当前权限不允许。这些都是有价值的事实，但
Learning Knowledge 目前没有接收它们；它的 active Query 只提供 loading、error
和 retained-data 事实。

今天若在该页面使用 `partial`、`not-configured` 或 `permission-limited`，只能从
空数组、按钮可见性或其他间接信号推断，反而削弱这个组件追求的语义诚实。在生产
调用方提供相应权威事实以前，这三种状态不应被描述为 Learning Knowledge 的当前
保证。

## 供 owner 审批的三个选择

| 选择 | 后果 | 评估 |
|---|---|---|
| 原样保留八状态 Contract | 继续把八种词汇写成当前保证，即使其中三种没有生产调用方事实 | 不推荐 |
| 将 Learning Knowledge 的当前 Contract 收窄为 `loading / available / empty / unavailable / stale` | 规范主张与当前生产证据一致；其余组件分支在单独接线前不构成 Contract | 推荐 |
| 整体回退 Wave B-01 | 恢复旧的零行呈现，并失去 unavailable/stale 的诚实区分 | 除非否定共享状态方向，否则不推荐 |

推荐选择刻意避免代码来回折腾：先收窄规范 Contract，现有通用呈现分支保持 dormant。
未来采用 `partial`、`not-configured` 或 `permission-limited` 时，必须先给出逐 endpoint
故事、权威事实来源，并在实现前取得 owner 明确批准。

## Owner 决定

2026-08-10，owner 在阅读上述故事和推荐后明确批准“收窄”选择。正式 Dashboard
Data Presentation Contract 因此只把 `loading / available / empty / unavailable /
stale` 写成 Learning Knowledge 当前保证；其余三个呈现分支继续保留在源码中，
但不构成当前 Contract。

## 审批门与下一边界

该 owner 决定只授权修正当前 Contract 文档，其他 DataTable 调用方保持不变，
也不授权把未接线状态扩展到生产页面。

该修正完成审查后，下一份推荐的 TickFlow 契约是最小 OHLCV Provider
Observation/Result Contract：覆盖 requested/actual timeframe、实际 provider/venue/
observed time、accepted/rejected rows，以及消费者拒绝不兼容 resolution 的规则。
持久 capability registry、provider 选择重写和执行权限变更都不属于该契约。

Risk 批准、Execution Gateway 所有权、Live 默认值、SQLite 权威、polling cadence
和后端数据语义在本次审查中全部保持不变。
