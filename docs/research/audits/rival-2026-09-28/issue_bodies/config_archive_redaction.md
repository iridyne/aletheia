# [backup][sync][security] Shared config redactor exports webhook HMAC secrets

## Why

共享 redactor 漏掉 Webhook HMAC 签名密钥，backup 与 sync 归档会携带签名权限；sync 的 secrets_included=false 因而不真实。根因应在共享边界修复，两个消费者都必须验收。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/config/schema.py:1241](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/config/schema.py#L1241) 定义 webhook.secret；[core/aletheia/notifications.py:207](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/notifications.py#L207) 用它签 HMAC。
- [core/aletheia/config/redact.py:22](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/config/redact.py#L22) 仅处理 network.proxy_url 与 market_data.coinalyze_api_key，deepcopy 后 webhook.secret 原样保留。
- [extensions/sync/exporter.py:142](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/extensions/sync/exporter.py#L142) 写 redactor 输出，manifest 声明 secrets_included=false。
- **当前 backup 路径已经迁移**：[ops/backup/service.py:369](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/ops/backup/service.py#L369) 同样调用 redactor；旧正文 core backup service 路径不能用于定位当前实现。`tests/test_backup_config_redaction.py` 的实际 import 也指向 ops.backup.service。
- 现成 [core/aletheia/config/secrets.py:107](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/config/secrets.py#L107) is_secret_key 可复用；不需要读取或修改 secret store。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

在 redact_config_dict 统一脱敏 credential-bearing 配置字段，复用现有 secret-key 分类器并支持 mapping/list 嵌套；保留 proxy URL 的结构化用户名/密码移除及原输入不变。核对 token 等普通数值字段被分类器过宽命中的情况，避免随意屏蔽后宣称无影响；不能以保留计数字段为由放过实际凭证。

共享函数的修复必须同时经 BackupService 和 ExportService 真实归档消费者验证；不在各入口复制字段名单。读取/解析失败保留当前明确 config_status 语义，不回退写入原始配置。

## Non-goals

不轮换真实密钥，不删除/重写已有归档，不修改通知签名算法、secret 存储权限、恢复策略或归档格式。不要顺手处理导出 UUID/不完整发布问题。

## Affected Authorities

[docs/design/contracts/persistence_recovery_contract.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/design/contracts/persistence_recovery_contract.md)；[docs/operations/development/agent-guides/SAFETY_AND_TRUST.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/operations/development/agent-guides/SAFETY_AND_TRUST.md)

## Verification — 本项验收

- 扩展 `tests/test_export_redaction.py` 与 `tests/test_backup_config_redaction.py`，用 WEBHOOK_SENTINEL 合成配置生成两种隔离归档，解压后逐文件检查不存在 sentinel，普通非秘密字段保留。
- 验证嵌套 mapping/list、大小写/分隔符变体、proxy 用户名/密码、旧 coinalyze 字段；原始输入对象不变。
- 不以扫描压缩字节替代解压内容检查，不使用 `or True` 等空断言。manifest/config_status 与实际内容一致；malformed config 不泄露原文。
- 聚焦用例建议命令：`uv run pytest tests/test_export_redaction.py tests/test_backup_config_redaction.py -q`，新增断言须在修前失败。

## 执行授权与交付边界

Owner 于 2026-09-28 要求将已审查修复整理为可独立领取的 issue，并明确期望领取 Agent 完成修复、验证、PR 和合并到 `master`。本条记录该范围内的授权：承担本 issue 的 Agent 应完成该 D0 修复及必要测试、自审、提交、推送、review-ready PR，并在仓库适用门禁满足后合并到 master、核实实际合并结果；不需要在每个常规工程步骤重复请求批准。**Issue 本身不覆盖更高优先级约束，也不授予新的 D1/D2、部署、重启共享 Gateway、修改真实凭证、Live 解锁、历史数据重写或资格窗口重置权限。**

从领取时最新 master 创建隔离分支/工作区，先读该版本 AGENTS.md、SYSTEM_MAP 和匹配指南，核对本 issue 的函数与生产 caller。本文固定 SHA 是证据，不要求从旧 SHA 开发。若该根因已有在途 PR，复用/协调，避免两个 Agent 同时修；若当前 master 已满足，提交实际代码及验收证据说明，不制造空 PR。保留他人改动。

## 验证与合并完成条件

- 使用合成数据、临时目录/SQLite、替身 provider。不得读取真实密钥、连交易所或使用主库来复现。
- 为下文验收建立最小行为回归，证明原行为失败、修复后同一条件通过；不要只匹配源码字符串，也不要跳过或弱化失败断言。
- 首轮反馈执行 `uv run python scripts/dev_check.py`，再执行下文聚焦用例及改动所需的本地 PR 验证。具体 selector 以当前真实测试收集结果为准，零收集/skip 不算通过。不要在 macOS 跑全套；全套不是本 issue 的默认要求。
- 按 `.github/PULL_REQUEST_TEMPLATE.md` 填 PR body，执行 `uv run --no-sync python scripts/ci_preflight.py --pr-body-file <实际文件>` 和当前 `scripts/ci_local.py` 对该变更要求的本地检查；保留命令、结果、失败日志及其对应 HEAD。此处 `<实际文件>` 是领取者创建的 PR body 文件，不能原样执行。
- 远程 GitHub CI 在本次复核的 root AGENTS.md 中已退役，不能启动或等待它。遵循领取时适用规则；若分支保护仍要求不可用检查，报告具体阻塞，不绕过、不改保护、不伪造绿灯。
- 按 PR_REVIEW_LIFECYCLE 处理当前 HEAD 上的有效反馈，记录 `MERGE_READY_SHA`。合并前重新核对 PR HEAD、目标 master、测试证据及硬阻塞；若解决冲突产生新代码，重新验证受影响路径。
- 完成必须提供：修复 commit、PR URL、实际 `MERGED` 状态、merge commit SHA、更新后的 master 已包含该 merge 的证据，以及本 issue 验收结果。开启自动合并、PR 可合并、测试通过均不等于已合并。合并并核实后关闭 issue；真实门禁阻塞时保持未完成并报告，不能提前关闭。

Runtime / Economic Verification：本条交付是仓库级契约修复与合并，不要求部署或真实经济实验；明确记录 Fedora 未验证，不声称线上已修好。

<details>
<summary>历史 issue 正文（保留原始证据；旧路径、数量、建议以本次复核为准）</summary>

Reviewed current `origin/master@134c35a8ccfa6345d29bd235f8a4b314299b9de3` on 2026-08-22.

## Problem

The shared config redactor does not redact `webhook.secret`, even though that value is the HMAC signing key for outbound Webhooks. Managed backups and sync exports both write the redactor output into portable archives. Sync additionally publishes `secrets_included: false`, making the manifest factually false.

## Evidence and deterministic proof

- `aletheia/config/schema.py:988-997` defines `WebhookConfig.secret`.
- `aletheia/notifications.py:161-175` uses it to compute the SHA-256 HMAC signature.
- `aletheia/config/redact.py:22-31` handles only proxy URL credentials and `market_data.coinalyze_api_key`.
- `aletheia/config/secrets.py:77-102` already has the recursive secret-key classifier `is_secret_key()`.
- `aletheia/backup/service.py:149-164` writes the shared redactor output to managed backup.
- `aletheia/sync/exporter.py:91-103` writes the same output to sync bundles; `:273-287` declares that no secrets are included.

Current behavior is directly demonstrated by:

```python
from aletheia.config.redact import redact_config_dict
assert redact_config_dict({"webhook": {"secret": "WEBHOOK_SENTINEL"}})["webhook"]["secret"] == "WEBHOOK_SENTINEL"
```

## Impact

Each backup/export creates another transportable plaintext copy of the signing authority. Disclosure of either archive lets a holder forge Webhook signatures. Operators relying on `secrets_included: false` may handle the bundle as non-secret data.

## Root cause

The shared redactor is a two-field allowlist rather than a recursive trust-boundary scrub, despite an existing centralized secret-key classifier.

## Minimal repair path

Make `redact_config_dict` recursively traverse mappings/lists and mask every value whose key matches existing `is_secret_key()`. Preserve the current structural proxy-URL credential removal. Do this once in the shared helper; do not add separate backup/export redactors.

## Regression proof

- Add `webhook.secret=WEBHOOK_SENTINEL` to both backup and sync redaction fixtures.
- Unpack each archive and assert no config value contains the sentinel while non-secret sibling fields survive.
- Assert the sync manifest's `secrets_included: false` remains true of the actual contents.
- Retain current proxy and Coinalyze coverage.

## Classification and duplicate check

Class A, severity high: restores an existing credential boundary. Full open/closed searches found no webhook-secret backup/export issue. Closed #1175 repaired only Coinalyze/proxy credentials; #1010 concerned other naming/redaction paths.



</details>
