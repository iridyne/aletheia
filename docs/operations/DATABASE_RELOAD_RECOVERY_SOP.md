# 数据库重载后的学习证据恢复 SOP

> 状态：人工执行的低频维护 SOP；不是自动化 feature，也不改变交易策略代码。
> 适用：数据库 swap、重建或重载已经使 paper account / proposal / order / fill / position
> 因果链中断，但 `universe_forward_outcomes` 或知识库仍保留的事故。
> 不适用：普通 schema migration、可证明未断开交易因果链的 gateway 重启、日常数据清理。

## 目的

保留旧数据的研究价值，同时立即阻止“旧账户/旧实验窗口的结果型证据”继续被当前
System Operator（PI）当作实时 conviction 依据。

这不是删除历史：完整的重载前状态先进入受验证的备份；知识条目在活动库中改为
`archived`，仍可检索和恢复。由于现有 `family_direction_edge()` 直接查询活动的
`universe_forward_outcomes`，且该表没有可用的 `archived` / epoch 字段，旧 outcome 必须从
**活动库**移出，才能在不改代码的情况下停止影响 PI。

## 不变边界

- 不修改 PI prompt、conviction 门槛、ranker、Risk、Execution、exchange、Live 或凭据。
- 不强制开仓；重载后的冷启动应呈现为“历史 edge 不足”，不是正/负 edge。
- 不修改任何原始 PnL、order、fill、position 或 audit 事实来伪造连续性。
- 不自动处理 `ps_lessons`、`trading_guard` 或其他可能有安全/约束语义的记录；它们须单独
  人工审阅，不能由本 SOP 批量归档。
- 不把这份 SOP 接入 scheduler、self-heal 或 AI agent；每次执行都需要 owner 明确批准。

## 核心规则

“保留”与“实时可消费”是两件事：

| 数据 | 重载前保留方式 | 当前 PI 是否可消费 |
| --- | --- | --- |
| `universe_forward_outcomes` | 受验证的完整备份 | 否；活动库只保留 reset 后入场的行 |
| `trade_case_study` | `knowledge_items.status='archived'` + 备份 | 否 |
| `trading_memo` / `reasoning_feedback` / `calibration_report` | `archived` + 备份 | 否；由新窗口重新生成 |
| `guard_candidate` | `archived` + 备份 | 否；它是 PI advisory，不是已批准的硬 guard |
| `ps_lessons` / `trading_guard` | 原样保留，另案审阅 | 不由本 SOP 决定 |
| `research_note`、当前行情、当前 regime、重载后机会 | 原样保留 | 按各自新鲜度与既有契约处理 |
| `filter_decisions` / `filter_candidate_outcomes` | 原样保留作审计 | 本 SOP 不改变其消费者 |

## 一次性恢复流程

### 0. Owner 决策与边界记录

在任何写入前，由 owner 在 incident / 运维记录中固定以下值：

```text
reset_at_utc: <数据库因果连续性失效的准确 UTC 时间>
reason: <database swap | rebuild | reload>
account_chain: broken
approval: <owner + 时间>
```

`reset_at_utc` 取“旧交易链不再可信”的时点，而不是发现问题、重启完成或人工开始操作的
时间。本次 2026-07-14 事故的候选边界是 `2026-07-14T03:12:00+00:00`，执行前仍须由 owner
确认。

### 1. 冻结并建立可回滚快照

1. 记录 `uv run aletheia gateway status` 和 `uv run aletheia doctor` 的输出。
2. 在 gateway 仍可访问时，通过受管 persistence API 创建带标签的完整备份：
   `POST /api/persistence/backups`，例如 label 为 `pre-reset-recovery-YYYYMMDD`。
3. 通过 `POST /api/persistence/backups/verify` 验证返回的 archive；验证失败立即停止。
4. 执行 `uv run aletheia gateway stop`，并再次确认状态为 stopped。不得在 scheduler/PI
   继续写 SQLite 时执行第 3 步的变更。

备份是研究档案和唯一回滚点；不要手工复制 WAL 中的 `app.db` 文件代替受管 SQLite backup。

### 2. 只读预览并签字

在停机状态，对活动库运行只读统计，记录预期影响数。至少核对：

```sql
-- A. 将退出活动 PI edge 窗口、但仍在备份中保留的旧结果
SELECT COUNT(*)
FROM universe_forward_outcomes
WHERE entry_ts < :reset_at_utc;

-- B. 将归档、而非删除的旧 PI prompt 资料
SELECT item_type, COUNT(*)
FROM knowledge_items
WHERE created_at < :reset_at_utc
  AND status = 'active'
  AND item_type IN (
    'trading_memo', 'reasoning_feedback', 'calibration_report',
    'trade_case_study', 'guard_candidate'
  )
GROUP BY item_type;

-- C. 必须保持为零的交易链写入；若非零，停止并按交易恢复事故处理
SELECT
  (SELECT COUNT(*) FROM trade_proposals) AS proposals,
  (SELECT COUNT(*) FROM risk_decisions) AS risk_decisions,
  (SELECT COUNT(*) FROM orders) AS orders,
  (SELECT COUNT(*) FROM fills) AS fills,
  (SELECT COUNT(*) FROM positions) AS positions;
```

若 C 非零，本 SOP 不适用：先处理真实订单/持仓的一致性，不得借“重载恢复”隔离学习数据。

### 3. 一次性活动数据隔离

只有 owner 已签字、备份已验证、预览数已记录且 gateway 已停止时，执行一次事务：

```sql
BEGIN IMMEDIATE;

-- 旧结果离开活动表；它们仍存在于已验证的备份 archive 中。
DELETE FROM universe_forward_outcomes
WHERE entry_ts < :reset_at_utc;

-- 旧的、已追溯到 PI prompt 的派生资料留在 knowledge_items，但不再被默认 active 查询读到。
UPDATE knowledge_items
SET status = 'archived', updated_at = :operation_at_utc
WHERE created_at < :reset_at_utc
  AND (status = 'active' OR status IS NULL)
  AND item_type IN (
    'trading_memo', 'reasoning_feedback', 'calibration_report',
    'trade_case_study', 'guard_candidate'
  );

COMMIT;
```

不得用下列方式“快速修绿”：改写 `entry_ts` / `settled_at`、把旧 `net_return_pct` 置零、伪造
新的 outcome、删除 audit、删除 lesson、或把 old trade 伪装成 maintenance close。它们会毁掉
研究可解释性。

### 4. 变更后验证

在恢复 gateway 前，再次运行：

```sql
SELECT MIN(entry_ts), MAX(entry_ts), COUNT(*)
FROM universe_forward_outcomes;

SELECT item_type, status, COUNT(*)
FROM knowledge_items
WHERE item_type IN (
  'trading_memo', 'reasoning_feedback', 'calibration_report',
  'trade_case_study', 'guard_candidate'
)
GROUP BY item_type, status;
```

验收条件：

1. 活动 `universe_forward_outcomes` 没有 `entry_ts < reset_at_utc` 的行；
2. 上述旧 prompt 类型不再是 active；
3. `ps_lessons`、`trading_guard`、`research_note`、audit 和交易链表未被本操作写入；
4. 备份验证结果、预览计数、实际删除/归档计数和操作者身份已写入 incident 记录。

随后使用 `uv run aletheia gateway start` 启动，并依次检查：

```text
uv run aletheia doctor
uv run aletheia gateway status
```

观察 PI 的第一批 context：它可以仍因当前 `SIDEWAYS` 或候选质量而 skip，但不能再引用
reset 前的 family edge、memo、case、reasoning feedback 或 calibration 作为当前窗口的证据。

### 5. 回滚

若验证失败、发现 reset 边界错误、或发现交易链并非空：保持 gateway 停止，使用第 1 步已验证
的 persistence backup 走受管 restore 流程恢复；不得以文件复制覆盖活跃 SQLite/WAL。恢复后先
进行只读审计，再重新裁定是否执行本 SOP。

## 后续重载的最小检查表

每次低频重载只需重复以下人工步骤，不新增代码或常驻服务：

1. 先判断是否真的断开了账户/决策因果链；未断开则不运行本 SOP。
2. 写下准确 UTC 边界和 owner 批准。
3. 创建并验证备份。
4. 停 gateway，预览，单一事务隔离旧活动 outcome 与已追溯的旧 prompt 内容。
5. 启动 gateway，doctor/status 验证，并在 incident 中记录前后计数。
6. 把旧数据留在备份/archived 层供研究；新窗口不足时接受 `insufficient`，绝不以旧负 edge
   填补空白，也不以零交易为理由强制开仓。
