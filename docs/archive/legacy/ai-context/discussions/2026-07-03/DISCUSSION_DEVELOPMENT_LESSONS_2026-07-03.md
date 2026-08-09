# Discussion Batch Development Lessons - 2026-07-03

本文沉淀自 2026-07-03 owner direction 这一轮大批量修复。其长期取舍已整理至
[Product Direction](../../../../../product/definition/DIRECTION.md)。目标不是复述每个提交,而是给后续 AI
继续开发时使用的操作经验,避免重复踩坑、重复测量、重复开分支。

## 1. 先把长问题拆成可验证清单

owner 的长文通常不是一个任务,而是一组相关问题。不要直接开始改代码。

推荐流程:

1. 读 [Product Direction](../../../../../product/definition/DIRECTION.md) 和 `docs/_ai/discussions/2026-07-03/DISCUSSION_ACTION_PLAN.md`。
2. 拆成稳定编号,例如 D01-D15。
3. 每一项写清楚:
   - 现象是什么。
   - 可能影响哪个交易链路。
   - 需要改代码、只审计、还是需要 owner 决策。
   - 可量化结果是什么。
4. 审计项和修复项分开。审计分支只读或只加报告,修复分支才改行为。
5. 最后用集成分支统一验证,不要把多个未验证小分支直接堆到 `master`。

本轮有效做法:

- D01-D15 矩阵让问题从"感觉系统慢、学习闭环断"变成可验证事项。
- 每个分支只解决一个问题或一个紧密问题组。
- 最终用 `DISCUSSION_FINAL_STATUS_2026-07-03.md` 汇总已修、部分完成、需 owner 决策。

## 2. 量化优先,不要靠体感猜慢点

前后端延迟问题最容易被误判。先测量,再优化。

本轮结论:

- 实测 29 个 live path,每个 10 次,共 290 次 HTTP 请求。
- 最大慢点不是静态 JS,而是 `/api/operator/recommendations`。
- 该接口平均 492 ms,p50 476.4 ms,p90 545.5 ms,但 payload 只有 2.3 KB。
- 根因是一次请求内重复调用 `list_artifacts(limit=500)` 6 次。
- 修复后重复 artifact scan 6 -> 1,减少 83.3%。

后续规则:

- 先列出页面 mount 时会发哪些请求。
- 先测 HTTP 时间,再看 React render。
- 不要先上复杂 UI loading 或 code split,除非数据证明静态包是主瓶颈。
- 对慢接口加 focused regression,锁住"重复工作不得回归"。

相关文档:

- `docs/_ai/audits/FRONTEND_RUNTIME_LATENCY_AUDIT_2026-07-03.md`
- `docs/_ai/engineering/FRONTEND_LATENCY_TOP_OFFENDERS_FIX_2026-07-03.md`
- `docs/_ai/workflow/ASYNC_PERFORMANCE_LESSONS.md`

## 3. 前端源码更新不等于线上 UI 更新

本项目 gateway 服务的是 `aletheia/dashboard_static/`。改 `frontend/src` 后,浏览器不会自动看到新
UI,除非重新 build 并重启 gateway。

正确顺序:

```powershell
cd frontend
npm run build
cd ..
uv run aletheia gateway restart --no-browser
uv run aletheia gateway status
```

验证静态包是否真的更新:

```powershell
rg -n "Research|Decision|Portfolio|Universe Board" aletheia\dashboard_static\assets\index.js
```

如果静态包已经有新字符串,但浏览器仍显示旧 UI,先让 owner `Ctrl+F5` 硬刷新。不要继续改源码。

本轮踩坑:

- 侧边栏代码已经合并到 `master`,但浏览器仍显示旧侧边栏。
- 原因是 `dashboard_static` 仍是旧 build。
- 重新 `npm run build` + gateway restart 后,静态包确认包含新版 IA。

## 4. worktree 多不是问题,不收尾才是问题

大批量并发开发必须用 worktree,但 worktree 结束后必须清理。

推荐节奏:

1. 每个问题一个 worktree 和一个分支。
2. 分支名直接表达边界:
   - `audit/...` 表示只审计或只加报告。
   - `fix/...` 表示改行为。
   - `docs/...` 表示只改文档。
   - `integration/...` 表示合并验证。
3. 合并前确认 worktree 干净。
4. 合并到 `master` 后删除已合并 worktree。
5. 删除已并入 `master` 的本地分支。
6. 检查 `.claude/worktrees/` 下是否还有未登记旧目录。

安全清理原则:

- `git worktree list --porcelain` 是 git 认可的真相。
- 删除目录前确认路径在 `.claude/worktrees/` 下。
- 未登记旧目录也要检查是否有文件。
- 如果发现源文件残留,先和 `master` 同名文件 diff,确认是否只是陈旧副本。
- 不删除未合并分支,不删除无法解释的用户文件。

本轮结果:

- 删除已合并 worktree 25 个。
- 删除未登记旧目录 9 个。
- 删除已合并本地分支 33 个。
- 最终只剩主仓和 `master`。

相关文档:

- `docs/_ai/workflow/GIT_WORKTREE_PLAYBOOK.md`

## 5. 运行态事实不要写成 git 事实

`master` 里只能写意图、设计、决策和可复现验证。不要把某台机器当时的状态写成长期事实。

可以写:

- "接口 baseline 是在 2026-07-03 本机采样得到"。
- "需要部署后重新刷新 Universe 再采样"。
- "当前代码路径已由隔离测试证明"。

不要写:

- "gateway 现在正在跑"。
- "生产 Universe 已刷新完成",除非文件明确是运行态采样报告且不会被当成长期事实。
- "demo 账户当前处于某状态"。

后续判断当前状态时,永远重新运行:

```powershell
uv run aletheia doctor
uv run aletheia gateway status
```

相关文档:

- `docs/_ai/workflow/DEV_PROTOCOL.md`

## 6. owner 决策项不能伪装成 bug fix

有些问题不是"修不修",而是"产品和风险语义怎么定"。

本轮必须问 owner 的事项:

- `reduce_size` 是否从建议变成硬风控。
- DB retention/archive/vacuum 策略。
- 侧边栏一级结构。
- 历史 `PI` 技术标识符是否迁移。
- System Operator 是否允许自动处理 runtime issue,以及动作白名单。

已确认的决策:

- `reduce_size` 保持建议模式,不自动减仓,不变成硬风控。
- DB 使用保守归档模式:交易链路、订单、成交、仓位、风控、审计永久保留;只清理临时、缓存、遥测、可再生成数据。
- 侧边栏采用 trading-desk IA:
  - Global: Overview, Command Center.
  - Research: Universe Board, Markets, Prediction Markets, Serenity Lens, News Intel.
  - Decision: Approval Desk, Agent Activity.
  - Portfolio: Trades & Positions, Risk & Controls.
  - Global: Learning, Settings.

经验:

- 风控、DB retention、历史审计迁移,不能由 AI 自行决定。
- owner 选项要短、互斥、能说明后果。
- 选择确定后,再写文档或测试锁住。

## 7. 信任边界先写测试,再改行为

触及以下路径时不要直接改:

- `aletheia/risk/`
- `aletheia/execution/`
- live unlock
- credentials
- order path
- position/adoption/reconciliation 的交易语义

本轮正确做法:

- limit entry 四要素属于交易和风控边界,先补 focused tests,再改 Risk Engine 和 proposal 字段。
- doctor runtime supervision 只加诊断上下文,明确不自动改代码、不解锁 live、不下单。
- `reduce_size` 没有 owner 硬授权,保持 advisory。

最低要求:

```powershell
uv run pytest tests/specs/ -q
uv run pytest <focused_test_file> -q
```

## 8. pytest 优化要先分层,不要先大改 fixture

本轮对 pytest 的有效结论:

- collection 本身不慢,full collection 约 4.85s。
- 代表性 423 tests 约 54.36s。
- 真正慢点是少数测试,不是所有 fixture 都该重写。
- 已新增分层/慢标签/慢测试分类工具。

后续策略:

- 日常只跑相关 focused tests。
- 风险/执行/信任边界跑对应 specs。
- 提交前后台跑全量。
- 慢测试先标记、分类、记录原因;不要为了速度删验证。

相关文档:

- `docs/_ai/audits/PYTEST_FIXTURE_SPEED_AUDIT_2026-07-03.md`
- `docs/_ai/engineering/PYTEST_TIERING_SLOW_LABELS_2026-07-03.md`
- `docs/_ai/engineering/PYTEST_SPEC_REDLIGHTS_2026-07-03.md`
- `docs/operations/development/PYTEST_OPTIMIZATION_GUIDE.md`

## 9. Universe 修复要分清三种证据

Universe 问题容易出现"测试绿但生产没刷新"的错觉。

三种证据不能混用:

1. 合成池测试:证明 allocator/scorer 逻辑正确。
2. 隔离 DB refresh:证明完整 refresh path 能产出合理分布。
3. 生产 DB 采样:证明当前运行库在部署和刷新后真的改善。

本轮状态:

- 逻辑和隔离 refresh 已证明。
- 生产库只做过只读采样,还需要部署后刷新再采样。

后续不要说"生产已修",除非完成:

```text
deploy/restart -> trigger Universe refresh -> read-only sample latest snapshot
```

相关文档:

- `docs/_ai/audits/UNIVERSE_POST_FIX_DISTRIBUTION_AUDIT_2026-07-03.md`

## 10. Learning 闭环要证明进了决策上下文

Learning 页面有数据,不代表 System Operator 会用它。

必须证明至少一条路径:

```text
closed trade / lesson / knowledge / analytics summary -> System Operator context -> prompt
```

本轮已证明:

- trade case studies 能进入上下文。
- applied `ps_lessons` 能进入上下文。
- Layer2 learning signals 能进入 System Operator prompt/context。
- closed-position trade analytics 进入 prompt,字段组从 0 -> 4:
  `totals`, `by_source`, `by_family`, `hold_distribution`。

后续如果要继续增强 Learning,优先补"消费证据",不要只做页面展示。

相关文档:

- `docs/_ai/audits/LEARNING_TO_OPERATOR_CONTEXT_AUDIT_2026-07-03.md`
- `docs/_ai/session/OPERATOR_TRADE_ANALYTICS_CONTEXT_2026-07-03.md`

## 11. 命名统一要区分用户显示和技术历史

owner 希望 PI Agent 改成 System Operator。要分清:

- 用户可见 UI、prompt、活文档:应尽量改成 System Operator。
- DB 表、历史审计、旧 spec id、模块文件名:不应随便迁移。

原因:

- 历史审计和 causal chain 需要连续。
- 技术标识符迁移会牵涉大量数据和测试。
- 不影响用户理解的历史 ID 可以保留,并在文档说明。

后续如果要彻底迁移技术标识符,必须单独开迁移设计和回归测试。

## 12. TypeScript 迁移不是前后端延迟的根因

owner 曾提出是否把 Python 后端迁到 TypeScript。当前结论是不迁移。

原因:

- 这轮延迟主因是请求 fan-out、重复后端读取、缓存策略和同步阻塞风险。
- 语言迁移成本极高,且会触及交易、风控、执行稳定性。
- 更安全的替代方案是 OpenAPI schema/export 和前端类型生成。

本轮结果:

- OpenAPI export 当前有 265 paths,87 schemas。
- 保留 Python 后端,加强 schema/type 边界。

相关文档:

- `docs/_ai/engineering/OPENAPI_TYPE_GENERATION_2026-07-03.md`

## 13. 推荐的下一次大批量修复流程

可直接复制执行:

```text
1. 只读阶段
   - git pull --ff-only
   - uv run aletheia doctor
   - 读 AGENTS.md / CURRENT_TASK.md / AI_HANDOFF.md / DEV_PROTOCOL.md
   - 读本文件

2. 拆问题
   - 把 owner 长文拆成 Dxx 清单
   - 标注: fix / audit / decision / deploy-verify
   - 每项写量化完成标准

3. 开 worktree
   - 每项一个 branch/worktree
   - audit 与 fix 分开
   - trust boundary 先 spec/test

4. 验证
   - focused tests 先绿
   - specs 按风险范围跑
   - 前端改动必须 npm run build
   - 需要上线 UI 时重启 gateway

5. 集成
   - integration branch 合并
   - full pytest 或核心矩阵
   - final status matrix

6. 收尾
   - 合回 master
   - 删除已合并 worktree 和本地分支
   - 更新 AI_HANDOFF / CURRENT_TASK / DEVLOG
   - 不把运行态事实写进 git
```

## 14. 当前仍值得继续的后续项

这些不是本轮文档任务要修的代码,但后续 AI 应优先识别:

- 部署后刷新 Universe 并采样生产分布。
- 按 owner 已选的保守归档模式,设计高容量非 causal 表的 retention 规则。
- 保持 `reduce_size` advisory-only,除非 owner 重新批准硬风控。
- 观察 Serenity 真交易关闭后的 post-mortem / feedback 闭环。
- 如继续优化前端延迟,先复测 `/api/operator/recommendations` post-restart p50/p90,再动下一个慢点。
