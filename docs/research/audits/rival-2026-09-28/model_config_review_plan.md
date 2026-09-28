# Model provider / config：分析与修复方案

日期：2026-09-28。源码基线 `0318e5efc6bca6ac5be3393c68fcd90cc5195d70`。主 Agent 独立审查，未访问真实密钥、调用模型、修改业务代码或运行行为测试。

## 覆盖与结论

model_provider：8 个 Python 文件、3,410 物理行、45 个 except；config：9 个文件、3,471 行、38 个 except。全文件 AST 与宽异常入口扫描，重点读取 registry、fallback、broker 的执行边界、router 健康投影，以及 loader 的读取/写回、redact、secret 字段分类和相关 schema。没有逐字段证明全部配置组合，也没有并发行为验证。

| 项 | 证据和根因 | 现有记录 | 分类 |
|---|---|---|---|
| M1 | `model_provider/fallback.py:229,461,478`：硬预算消费可选 shadow 观察结果；关闭 shadow 后没有评估结果，enforcement 直接返回 | [#1624](https://github.com/iridyne/aletheia/issues/1624) OPEN | D0，保持已批准预算含义 |
| M2 | `config/redact.py:22` 仅处理 proxy 与旧 coinalyze 字段，漏掉 `schema.py:1241` 的 webhook.secret；`extensions/sync/exporter.py:142,343` 输出该配置并声明 secrets_included=false | [#1379](https://github.com/iridyne/aletheia/issues/1379) OPEN | D0，恢复已有脱敏边界 |

本轮确认的是当前 sync 导出路径。#1379 还描述了历史 backup 消费者；本轮没有找到并证明当前 backup 同一路径，不能照搬为两个当前可达漏洞。scorecard 健康 list/dict 漂移见 [Core 报告 A4](core_analytics_economics_review_plan.md)，不重复计数。

**后续证据补充（2026-09-28，issue 正文准备）**：沿 `tests/test_backup_config_redaction.py` 的真实 import 找到已迁移的 `ops/backup/service.py:369`，它仍调用相同 redactor 并将结果写入归档。该文件在拉取的 master `a998070130d2ab892e9ba89b7de48886a1a708e4` 与本报告基线一致，因此 backup 和 sync 两条当前消费路径均已确认；实施 #1379 必须覆盖两者。上段保留原轮次证据边界，不再用“未找到”排除 backup。见 [已发布 issue 正文快照](issue_bodies/README.md)。

## M1：预算评估脱离可选观察开关

触发链：合法配置 economics.enabled=true、enforcement_mode=hard_block、budgets_shadow_enabled=false；`complete_with_fallback` 先调用 `_observe_would_block`，其提前返回；`_enforce_budget_if_configured` 读取不到 `_aletheia_would_block_observations`，继续模型请求。即使已耗尽且可准确计价的预算，也无法通过该路径阻止请求。tracing 关闭同样会使当前观察路径跳过，需一起覆盖。

最小修法：复用现有预算读取和 fallback 链成本估计，在需要 enforcement **或** shadow 时计算一次结果；shadow/tracing 仅控制证据记录，不决定硬预算是否评估。内部明确区分 within_budget、would_block、unavailable，可用现有结构或最小返回值，不新增一套预算服务。保持既有 safety caller 豁免、预算值、路由和模型选择不变。无法计价、读取失败的严格模式处置必须对齐现有预算 Contract；有歧义时仅隔离该语义决定，不默认当作预算充足。

验收：用 provider spy 证明 hard_block + shadow=false + exhausted 的 I/O 次数为零；shadow=true 的原路径同样阻断；warn_only + shadow=false 正常调用；分别覆盖 tracing 关闭、预算读取失败、未知报价、显式安全调用者。拒绝结果由现有异常/结果边界传播，不能被 fallback 作为临时模型失败转到另一家继续花费。并发预留是否满足硬上限需另核当前 Contract，本轮没有证明它已满足或另有缺陷。

## M2：在共享脱敏函数修复

`notifications.py:207` 将 webhook.secret 用作 HMAC SHA-256 签名密钥，故它不是普通配置字符串。redactor deepcopy 后不处理该字段；sync archive 因此能携带签名权限。本轮只读源码，未读取用户 config 或生成真实导出。

最小修法：在 `redact_config_dict` 使用现有 `config/secrets.py:is_secret_key` 对 mapping/list 递归脱敏，并保留 proxy URL 的结构化用户名/密码去除。不要在 exporter 再维护一份字段表。分类器可能覆盖 token 等非秘密计数字段；实施时用真实 schema 名称核对脱敏可用性，优先保密且明确导出内容，不能为恢复计数而放行真实凭证字段。备份若已改用其他导出机制，按实际 caller 处理，不复活历史代码。

验收：只用 `WEBHOOK_SENTINEL` 等合成值，原始输入不被修改；嵌套字典、列表、CamelCase secret 字段、proxy 认证串都被处理，普通 URL/业务字段保留。创建隔离 sync archive 后检查实际内容无 sentinel，manifest 的 secrets_included=false 与内容一致。不要打印真实秘密作为验证证据。已有归档轮换/清理属于独立数据操作，本计划不删除归档或自动轮换密钥。

## 顺序、边界与剩余工作

M2 可先作为独立小修复；M1 其次，与 Core economics 的预算查询共用一条实现。每项完成须有根因修复、合成数据的最小回归、调用链自审和 commit。当前仅完成分析和实施设计。

不因 schema.py 超过 2,000 行就拆包；本组没有 >300 行函数。config.loader 的原子保存、锁和 CAS 不能仅凭名称认定所有调用者都无丢更新；本轮未逐个验证 writer。fallback 无有效候选时的直连分支、broker 取消后资源释放、配置热切换竞争仍需生产可达性和专门并发证据，未作为确认缺陷。CLI 只读命令触发迁移写回已有 [CLI F2](cli_shitmap.md)，不重复立项。

## 全文件结构扫描清单

口径：物理行包含空行/注释；except 为 AST ExceptHandler；宽指 bare except 或捕获 Exception/BaseException。该清单证明结构扫描覆盖，不代表每个文件都完成调用链深审。

| 文件 | 物理行 | except / 宽 |
|---|---:|---:|
| `core/aletheia/model_provider/__init__.py` | 0 | 0 / 0 |
| `core/aletheia/model_provider/base.py` | 720 | 15 / 15 |
| `core/aletheia/model_provider/broker.py` | 873 | 13 / 1 |
| `core/aletheia/model_provider/fallback.py` | 663 | 7 / 7 |
| `core/aletheia/model_provider/guardrails.py` | 27 | 0 / 0 |
| `core/aletheia/model_provider/inference_contract.py` | 356 | 3 / 3 |
| `core/aletheia/model_provider/registry.py` | 96 | 0 / 0 |
| `core/aletheia/model_provider/router.py` | 675 | 7 / 3 |
| `core/aletheia/config/__init__.py` | 0 | 0 / 0 |
| `core/aletheia/config/exchange_alignment.py` | 75 | 0 / 0 |
| `core/aletheia/config/exchanges.py` | 102 | 0 / 0 |
| `core/aletheia/config/execution_costs.py` | 41 | 1 / 0 |
| `core/aletheia/config/loader.py` | 611 | 15 / 5 |
| `core/aletheia/config/prediction.py` | 15 | 0 / 0 |
| `core/aletheia/config/redact.py` | 31 | 1 / 1 |
| `core/aletheia/config/schema.py` | 2203 | 1 / 0 |
| `core/aletheia/config/secrets.py` | 393 | 20 / 12 |
