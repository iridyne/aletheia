# [cli][preview] seed-markets must select and verify isolated data root before database access

## Why

`aletheia dev seed-markets` 承诺只写隔离 Preview fixture，但模块导入时已固定数据根，命令内才设置 Preview 环境。终端显示 Preview 路径不能证明真实数据库目标已切换；后续存在 DELETE，因此该错位有破坏非 Preview 数据的风险。

## Current Facts / Evidence

复核日期：2026-09-28。固定 master 源码：`a998070130d2ab892e9ba89b7de48886a1a708e4`。本条所列根因文件已与前轮审计逐文件比对，缺陷相关实现未变化。证据属于源码与历史隔离探针，不是生产事故或运行环境观测。领取时仍须在最新 master 上复核。

- [core/aletheia/cli/dev_seed.py:22](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/cli/dev_seed.py#L22) 顶层导入 loader；`seed_markets:292` 才计算 Preview 根并写环境变量，随后 get_db、_clear 和多个 seed writer。
- [core/aletheia/config/loader.py:69](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/config/loader.py#L69) 的 DATA_DIR/CONFIG_PATH 在导入时固定，get_data_dir 返回该值；SQLite 使用该有效根。`os.environ` 后改值不重算模块常量。
- [core/aletheia/cli/dev.py:37](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/cli/dev.py#L37) 的 dev api 已采用配置好环境后启动子进程的隔离模式，可复用；不要在已加载主进程中热切换全局数据库根。
- 前轮隔离探针在导入前设临时主根、移除 Preview 环境，再调用原始 seed 并在 get_db 入口截断；观察到实际根保持原值、打印根不同。没有执行 DELETE 或访问真实主库。本次为当前源码比对，领取者需将其变成持久回归。
- 查重 seed-markets、seed + preview 未找到对应 issue；不与普通 dev API 隔离实现重复。

## Dependencies / Activation Conditions

无跨 issue 的实现前置条件。

## SDD Depth

D0 — 恢复已有行为契约。Contract Delta: NONE。不得通过修改 Contract 将缺陷合法化。

## 修复目标与技术落点

在 CLI 启动/子进程边界建立正确 Preview 环境，使配置、storage、secret 的首次导入看到同一个隔离身份。复用 runtime_paths 的 preview_instance_dir 与现有 Preview 校验；子执行入口保持内部用途，不能形成可绕过检查的公开写入口。

任何 DB 打开、迁移、清理或 fixture 写入前校验实际 get_data_dir 的解析路径等于所选 checkout/port 的 Preview 根，且在受准许的 Preview 子树内；失败立即非零退出。必须抵抗继承的 ALETHEIA_DATA_DIR、PREVIEW_MODE=0、不合法/越界路径及符号链接别名。不要仅将 setdefault 改成赋值，不要只校验打印变量。保留子进程退出码传播、fixture 内容与正常 Preview 端口身份。

## Non-goals

不启动/重启稳定 Gateway；不复制或删除主库；不读取真实 secret；不放宽 Preview 路径校验、不重写全局 loader 为可变单例，不改变 fixture 的业务含义。

## Affected Authorities

[docs/operations/development/DEVELOPMENT_PREVIEW.md](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/docs/operations/development/DEVELOPMENT_PREVIEW.md) 的 Instance identity and data isolation；[core/aletheia/runtime_paths.py](https://github.com/iridyne/aletheia/blob/a998070130d2ab892e9ba89b7de48886a1a708e4/core/aletheia/runtime_paths.py)

## Verification — 本项验收

- 扩展 `tests/test_dev_preview.py`，测试真实 `aletheia.main` 导入顺序与 CLI 入口，不能事先替换掉导致问题的 loader 初始化。
- 使用临时 HOME/合成主根和 Preview 根，在主根放哨兵。继承主 ALETHEIA_DATA_DIR 时，所有 DB/配置写入仍落在正确 Preview；主根哨兵与内容不变。
- 继承 PREVIEW_MODE=0、越界 Preview 根、路径符号链接、不同 port/checkout，分别证明拒绝或正确隔离；拒绝必须发生在 get_db/DELETE 之前。
- 正常 seed 成功、输出目录等于实际写入目录；初始化或子执行失败返回非零。secret 读取为 Preview 的空值，不触碰真实 keyring。

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
