# Audit Chain Contract — retired and historical sections

> **History (moved 2026-10-10).** Retired or superseded design kept as evidence; not current authority. See [design history](README.md).

Moved verbatim from [`audit_chain_contract.md`](../contracts/audit_chain_contract.md) when it was
rewritten into the numbered Contract form. Current behavior lives only in that Contract.

## Former doctor probe description (superseded: the probe now splits breaks at a fix timestamp)

## §5 验证工具

### aletheia doctor 集成

`core/aletheia/diagnostics/probes/storage_trust.py` 定义 `_probe_audit_chain()` 检查项，由 `doctor.py` 的 `build_report()` 探针列表挂入:

```python
def _probe_audit_chain(report: Report) -> None:
    from aletheia.files.jsonl import verify_audit_chain
    result = verify_audit_chain()
    records = result.get("records", 0)
    if records == 0:
        report.add("trust.audit_chain", "info", "no audit records yet")
        return
    if result["valid"]:
        reanchors = result.get("reanchors", 0)
        extra = f", {reanchors} rotation re-anchors" if reanchors else ""
        report.add("trust.audit_chain", "ok", f"hash chain valid ({records} records{extra})")
    else:
        errors = result.get("errors", [])
        detail = f"chain BROKEN — {len(errors)} error(s) in {records} records: {errors[:3]}"
        report.add("trust.audit_chain", "warn", detail,
                   "audit log may have been tampered with or corrupted")
```

**check 名称**: `trust.audit_chain`

**报告内容**:

| 状态 | severity | detail 示例 |
|------|----------|-------------|
| 无记录 | `info` | `"no audit records yet"` |
| 链完整 | `ok` | `"hash chain valid (3897 records, 2 rotation re-anchors)"` |
| 链中断 | `warn` | `"chain BROKEN — 8 error(s) in 3897 records: ['audit.jsonl:42: _prev_hash mismatch ...', ...]"` |

detail 中包含: 总记录数、错误数、前 3 条错误的 `{文件名}:{行号}: _prev_hash mismatch (expected <16hex>..., got <16hex>...)` 定位信息。

### 独立验证函数

`verify_audit_chain()`（`core/aletheia/files/jsonl.py`）可独立调用，返回:

```python
{"valid": bool, "records": int, "errors": list[str], "reanchors": int}
```

遍历所有历史文件 + 活跃文件（按 `_jsonl_paths_chronological` 顺序），对每行校验 `_prev_hash`。

---

## Former retired-Learning and funding rows of §6

旧 Learning lifecycle stable-ID 审计保持历史证据；新实验事件只由 Observation Lab 事务与 outbox 产生。

### §6.3 信任边界操作必须写审计记录

以下操作类型必须写入 `audit.jsonl` 记录，且必须在操作完成后持久化：
| Experiment lifecycle transition | Observation Lab lifecycle event/outbox | `observation_lab/canonical.py`；旧 Learning lifecycle 只读归档，不再推进 |
| Funding settlement/correction/terminal history expiry | `paper_position_funding_settled` / `paper_position_economics_repaired` / `paper_position_economics_corrected` / `exchange_position_funding_settled` / `exchange_position_funding_history_expired` | `portfolio/funding_audit.py` + `portfolio/broker_funding_settlement.py`；会计或 terminal-state 事务内写 stable-ID SQLite audit，JSONL mirror 独立幂等补写 |

### §6.3a Retired Learning lifecycle evidence

旧 `learning_lifecycle_events` 与 `learning-lifecycle:<event_id>` JSONL 记录保留原始身份。
旧 writer 与 reconciler 已退役；显式迁移必须验证 checkpoint、归档与 pending receipts，
不能删除未证明安全的证据，也不能重放历史 transition 获得当前授权。
新实验 lifecycle、撤销与交付由 Observation Lab 的现有事务/outbox 承担。

### §6.3c Funding settlement audit outbox

新发生的 paper/exchange funding settlement/correction，以及 exchange funding history terminal
classification，使用 versioned canonical tuple
`(position_id, event_type, funding_accounting_version, funding_economics_fingerprint,
transition_fingerprint, occurred_at)` 派生每次 transition occurrence 的 stable audit ID。
`transition_fingerprint` 绑定完整 before/after 事实。Settlement/correction 的 `occurred_at`
来自不可变会计 occurrence；history expiry 使用 `history_start + profile retention` 的确定性
边界，绝不使用重试处理时间生成新身份。Position/PnL、close projection、paper cash、Outcome
hook intent 与同一 settlement/correction `AuditEvent` 在一个 SQLite transaction 内提交；
history expiry 只原子提交 Position terminal reason 与同一 `AuditEvent`，保持 PnL/close
projection/cash/hook 不变。JSONL mirror 失败不得回滚或重放这些事实。

`audit_events.jsonl_mirror_status/jsonl_attempt_count/jsonl_last_error_class/jsonl_mirrored_at`
仅由 opt-in funding audit 使用。`NULL` 表示 unmanaged；历史与普通 audit 保持 `NULL`，不得因 migration
猜测旧 JSONL 是否缺失。新 funding audit 从 `pending` 开始；失败转 `failed`，成功单调转
`mirrored`。`funding_audit_reconciler` 每 5 分钟在专用 audit lane 有界处理 100 条。append 成功但
ack 前崩溃时，重启通过所有轮转文件中的同一 stable ID 直接 ack，不追加第二条记录。

会计结果与镜像结果正交：`settled/repaired/corrected/already_current` 不因文件故障改写；调用方同时
返回 `audit_mirror_status`。`already_current` 只愈合已有 pending audit，不为没有 stable audit 行的
历史 Position 编造事件。backlog 使 `/api/health` degraded，并由 Doctor
`trust.audit_mirror_backlog` 报警；`/api/ready` 与前后端 `/_infra/readyz` 仍只表达进程/静态就绪。

## Former proposal-chain references (TaG P0-1)

### §6.4 提案级 hash chain（TaG P0-1，2026-06-29）

**SDD 锚点**

- `tests/specs/spec_data_model.py` 的 DM-S08（pydantic 模型字段）/ DM-S09（migration 注册）/ DM-B06（insert 自动填 + 链入）/ DM-C02（re-compute 确定性 + 链完整性 + status 免疫）
- `tests/specs/spec_pipeline_adopted_position.py` 的 PL-ADOPT-09（audit event payload 携带 commit_hash + parent_hash）

**手动校验命令**

```
uv run pytest tests/specs/spec_data_model.py -k "hash" -q
uv run pytest tests/specs/spec_pipeline_adopted_position.py -k "commit_hash" -q
```

**设计参考**: `docs/research/agents-learning/OPENALICE_TAG_DEEP_DIVE_2026-06-29.md §五 P0-1 / P1-2`。

## Former §7 known defects

## §7 当前已知缺陷

### §7.1 哈希链中断（已知，未修复）

**症状**: 历史 JSONL 记录中存在少量 `_prev_hash` 断点，`aletheia doctor` 以 `trust.audit_chain: warn — chain BROKEN` 报告；具体断点数以当次 doctor 输出为准，不在契约中记录运行时读数。

**严重程度**: 🟠 中等——chain 中断削弱审计的防篡改保证，但不影响新记录的写入和系统运行。

**可能根因**: `_exclusive_file_lock` 于后期引入；更早写入的若干记录在无锁并发条件下产生了 `_prev_hash` 冲突，其中少数不满足 Pattern 2（consecutive duplicate）的容忍条件。

**历史证据**：[`docs/archive/legacy/issues/domain/BACKEND_ISSUES.md`](../../archive/legacy/issues/domain/BACKEND_ISSUES.md)（audit hash chain 实施历史）

**缓解状态**: 新记录自 file-lock 引入后不再产生竞态中断；历史断点为静态、已固定。未做 chain repair 工具。

### §7.2 warn 级别不阻塞启动

`trust.audit_chain` 中断时 doctor 报告 `warn`（非 `blocker`），网关启动不受阻断。若需要强制要求链完整性，需要将 severity 升级为 `blocker`。

### §7.3 提案级 hash chain 无 doctor 探针（TaG P0-1 范围外）

`trade_proposals.commit_hash` / `parent_hash` 链当前**仅由 SDD spec 测试**（DM-C02 单事务内重计算 + 链完整性）保证，**没有运行时 doctor 探针**——链中断不会触发 `aletheia doctor` 告警。

如需补齐，需新增独立扫描函数（类比 `verify_audit_chain()`），按 `rowid` 顺序遍历 `trade_proposals`，重算每行 `_compute_proposal_hash(p)` 对比存储值 + 验证 `parent_hash` 连续性，并接入 `diagnostics/doctor.py` 作为 `trust.proposal_chain` 探针。该工作不在 P0-1 范围内，列为后续 hardening 候选。

---
