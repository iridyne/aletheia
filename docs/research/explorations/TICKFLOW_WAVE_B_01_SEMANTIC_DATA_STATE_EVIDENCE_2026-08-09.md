# TickFlow Wave B-01：Semantic State 与 DataTable 契约证据 — 2026-08-09

## 结论

Wave B 第一项（候选 #2、#8）采用“共享呈现词汇 + 显式调用方事实”的最小边界，不建立新的全局状态管理或后端 envelope。

规范行为由 `docs/design/contracts/dashboard_data_presentation_contract.md` 持有；本文只记录本次研究、取舍和验证证据。

## 源码核验

- `DataTable` 原先只接收 `data[]`，零行统一显示 `emptyMessage`，无法区分首读 loading、失败无缓存、刷新失败但有旧数据或后端 partial。
- Learning 已有 `learningReadState()`，能够从 Query 的 loading/error/retained-data 事实区分 `loading / unavailable / stale / partial / available`。
- Universe 已有 disabled/fallback/stale 业务事实，但同时展示静态 fallback pool；本轮不把它强行迁入阻塞式 `not-configured`，避免隐藏合法 fallback 数据。
- 其他 DataTable 调用方尚未逐一证明配置、权限、coverage 或 freshness 来源，因此继续使用兼容行为。

## 契约取舍

共享词汇为：`loading / available / empty / stale / partial / unavailable / not-configured / permission-limited`。

- `empty` 只表示成功读取后的零条结果。
- `stale` 显示 retained rows；连接状态本身不能推导 freshness。
- `partial` 必须有 coverage 或 failed-section 事实，并继续显示可用行。
- `unavailable` 表示没有可显示的成功数据，不得降级成 empty。
- `not-configured` 与 `permission-limited` 必须来自明确事实，不能从空数组或按钮可见性猜测。
- 未传显式 state 的旧调用方保持零行→empty、非零→available，避免一次性全页迁移。

## 实施切片

- 新增 `DataStateMessage`，集中呈现上述状态和 alert/status 可访问语义。
- `DataTable` 新增可选 `state` 与 `stateMessage`；`stale/partial` 保留表格，其余非 available 状态不渲染表格。
- Learning Knowledge tables 成为第一个显式调用方，复用已有 `learningReadState()`，没有新建 Query 状态判断器。
- TanStack sorting、filter、pagination、row click 和 column visibility 均保持不变。

## 聚焦验证

```text
cd frontend
npm test -- --run src/components/DataTable.test.tsx src/lib/learningWorkbench.test.ts
# 2 files, 3 tests passed

npm run build
# TypeScript and Vite production build passed

uv run pytest tests/test_frontend_runtime_comm.py -q
# 9 passed

uv run python scripts/check_docs.py
# passed

git diff --check
# passed
```

没有在 macOS 运行全量 pytest，也没有把静态检查或 build 描述为运行时/收益证明。

## 保留边界

- Universe fallback、Trades/Positions reconciliation、Blotter ledger 等表格必须先审计各自 endpoint 事实，再单独迁移。
- 本轮不创建通用 response envelope、不增加状态库、不改变 polling cadence。
- `not-configured` 和 `permission-limited` 的具体调用方接入需要真实后端事实，不能为了覆盖枚举而制造推断。
