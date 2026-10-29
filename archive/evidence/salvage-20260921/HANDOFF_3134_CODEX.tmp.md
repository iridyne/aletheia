# #3134 → Codex 临时交接

## 目标与授权
完整执行 https://github.com/iridyne/aletheia/issues/3134 。Owner 原话：不要把 dependency domains 当成人工审批阶段；自主执行、验证、checkpoint 并选择下一批，直到全部 DoD 被当前代码和运行证据证明；只在明确的人类决策边界停止。此次只是切换工具，不缩减目标。直接接手本 workspace，不另起实现。

先读根 AGENTS.md、匹配指南、Issue **当前 body**。旧评论/本文/旧 todo 都不是完成依据。最近 checkpoint：https://github.com/iridyne/aletheia/issues/3134#issuecomment-5653307219 。它早于下面最后一次 lazy-import 改动。

## 当前工作区：必须保留
- 分支 `refactor/3134-product-truth`。
- HEAD `b1cfd7a7573af925520c85b86c1d043dc3e7cb58`，比本地 origin/master ahead 2（未 fetch 重验远端）。另一个本地提交 `454849ef3`；本地 origin/master 为 `98113da4d`（#3213）。
- 交接前 Git 报告：**562 staged、186 unstaged、8 untracked**（不含本文件）。这是已有大型 Scheme A 迁移和回归修复，不是垃圾。不要 reset/clean/stash 掉，不要只提交 index 中的旧版文件。
- `aletheia/` 已迁到 `core/aletheia/`；exchange 实现迁到 `extensions/`；pyproject、路径、打包和许多测试已改。整体迁移**尚未提交、未完整验证、未部署**。
- 不要把“所有东西搬进 core”当成 Scheme A 完成；optional research/data/model 实现仍有混合归属。

## 最近实际改动
1. `core/aletheia/analytics/entry_exit_geometry.py`：删除独立 Proposal hash 算法，复用 `repo.validate_trade_proposal_commit_hash`，避免遗漏 `entry_leg_index` 历史兼容规则。
2. 修复若干旧 fixtures 的必需 `execution_mode`；`tests/test_experience_loop.py` 的 Risk Critic 测试补显式模型选择前置。删除 `tests/test_execution_mode_identity.py` 中只检查 scheduler 源码字符串的测试，不替换成新的字符串锁定。
3. Exchange 目录/依赖收敛：
   - 原 `extensions/exchanges/ccxt/manager.py` → `extensions/exchanges/composition.py`。
   - 原 `ccxt/profiles/{bitget,binance,okx}.py` → 各自 `extensions/exchanges/<venue>/profile.py`。
   - 通用 adapter 仍是 `extensions/exchanges/ccxt/adapter.py`，基类在 `ccxt/profiles/base.py`；`ccxt/profiles/__init__.py` 已删除 venue 重导出。
   - production/tests/一个历史可执行 probe 的 imports 已迁移，无旧路径 facade；`tests/specs/spec_uta_boundary.py` 增加 generic CCXT 禁止 venue import 的检查。
4. **最后一次改动**：`composition.py::_profile_spec` 将具体 profile imports 移入各 execution_mode 分支。复现了仅导入 Bitget extension 就加载 Binance/OKX profiles；改后 Bitget import + funding retention 解析不再加载冻结 venues。没有更改账户选择、凭据范围、交易/结算语义。
5. `tests/test_diagnostics_honesty_fixes.py` 显式绑定被测 composition 的 registry factory，避免测试依赖其他测试提前注册 provider。

## 验证：不要混淆前后版本
- profile 搬迁后、最后 lazy-import 改动前：下列整组 **195 passed**；添加结构检查后单跑 UTA boundary **7 passed**。
- 最后 lazy-import 改动后：fresh-process smoke 通过，输出 retention `(90, '1')`，并断言 sys.modules 不包含 Binance/OKX 包。
- **最后重跑下面整组被取消，只执行到约 36%，没有最终通过结果。Codex 第一件事补跑它。**

```sh
uv run pytest -q tests/uta/test_ccxt_adapter.py tests/uta/test_execution_coordinator.py tests/uta/test_manager.py tests/specs/spec_uta_boundary.py tests/specs/spec_pipeline_multibroker_reconciliation.py tests/specs/spec_pipeline_tpsl_private_materialization.py tests/test_diagnostics_honesty_fixes.py --tb=short
```

其他已观察证据（各自当时 working tree，不能称最终 HEAD 全绿）：
- `tests/test_experience_loop.py`：11 passed。
- geometry / execution identity / exit intent / TP-SL 组合：45 passed。
- Model routing + SDK transport + retired providers：89 passed。
- `uv run python scripts/check_docs.py` 通过。
- 通用 CCXT fresh-process import 未加载任何具体 venue。
- **没有全套通过证据**。早前反复 macOS `pytest -x` 已停止，不继续这种跑法；当前指南禁止 macOS 全量测试。
- 交接时工具任务列表无 running job；最后整组为 cancelled。没有运行中的编辑 Agent。

## 仍需完成（不是已完成声明）
- capability-driven Core：清理可由 typed identity/capability 代替的 venue/provider 名称业务分支。
- HTTP endpoint discovery、manual IDs、bounded usability probe、显式 fast/deep/fallback 的端到端证据与实现归属。
- optional/frozen Extensions absent-safe、不直接写 Core authority、不拥有第二 lifecycle；`core/aletheia/server/app.py` 仍顶层导入很多 optional routes，是下一处检查入口。
- Legacy active authorities/fallbacks/control surfaces 的 reachability 证明与删除。
- 全 production ownership/writer/side-effect inventory；旧 inventory 明确写着 mixed/open，不能当全完成。
- #3135/#2899、Bitget Demo entry→close→成本完整→Outcome 的真实资格证据。
- #1884 operator plane：之前 Fedora 无 storm，slot attribution/stream isolation 运行证据未证明。历史草案 `/tmp/aletheia-1884-unpublished.patch` 未发布（使用前检查存在及适用性），不要不经归因就套补丁。
- 其余 Scheme A 归属/路径/打包和所有调用方收敛；无 forwarding aliases。
- 验证后按正常 commit/PR/checkpoint 流程推进，绑定 exact merged SHA，再按授权与部署规则取得 Fedora runtime 证据；最后逐条 DoD 审计。

## 关键入口与约束
- `docs/research/audits/SLIM_CORE_OWNERS_2026-09-13.md`：旧完整分类地图，混合归属/未证实部分明确列出，路径/基线部分过时。
- `docs/research/audits/SLIM_CORE_PRIVATE_IO_2026-09-13.md`：最后追加了 uncommitted 迁移说明；早期段落是历史证据。
- `core/aletheia/uta/manager.py`：Core port/provider binding；`extensions/exchanges/bitget/extension.py`：native reader/composition registration。
- todo 在长会话中多次失配，不能据其勾选宣称 DoD；当前暂停只是用户交接，不是新增审批门。
- Python 用 uv；定向测试使用隔离临时数据、禁止真实 credentials/network/order。macOS 不启动稳定 Gateway。远端 CI 已退休，不 dispatch/wait。
- Fedora 操作先读运行技能/部署指南；旧 Fedora commit 观察已过时，不是当前 runtime 真相。没有新增 Live、credentials、Risk/economic policy、破坏性历史迁移授权。
- 保持单一 writer、先 Risk 后 Gateway、unknown mutation 先 reconcile、历史 additive-only；不要靠放宽测试、兼容 facade 或新框架制造完成。

本文件为临时交接，不提交到产品仓库。接手后继续执行，不需要 Owner 重述目标。
