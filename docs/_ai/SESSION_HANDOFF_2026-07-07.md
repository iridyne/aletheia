# 项目状态交接快照 — 2026-07-07

> **目的**:记录当前多分支 / 多 worktree 的本地布局与进行中的工作,使远端可**完整复刻本地状态**。
> 本快照写在 `master` 上,任何分支 / 新克隆都能直接看到。
> 生成方:bug-fix 会话 · 维护:Aletheia Dev

---

## 1. 分支 × worktree × 远端 全景(截至本快照)

所有本地分支均已推送 `origin` 且完全同步(behind/ahead = 0/0)。

| 分支 | 检出于 worktree | 远端 | 说明 |
|------|-----------------|------|------|
| `master` | `C:/Users/ollama/Projects/aletheia-trading-os`(主) | ✓ | 主线 |
| `fix/bug-review-fixes` | `C:/Users/ollama/orca/workspaces/aletheia-trading-os/bug_fix` | ✓ | 全项目 bug 审查的修复分支(见 §2) |
| `iridite/data_source_enhance` | `C:/Users/ollama/orca/workspaces/aletheia-trading-os/data_source_enhance` | ✓ | 数据源增强(未并入 master,8 ahead) |
| `iridite/system_proxy_support` | `C:/Users/ollama/orca/workspaces/aletheia-trading-os/system_proxy_support` | ✓ | 系统代理支持(1 ahead) |
| `docs/api-reference-alignment` | `.claude/worktrees/api-reference-alignment` | ✓ | 1 提交:SDD alignment pause |
| `feat/trade-sizing-diagnostics` | `.claude/worktrees/trade-sizing-diagnostics` | ✓ | 1 提交:trade funnel + sizing diagnostics |
| `iridite/bug_fix` | 无 worktree(仅指针) | ✓ | 已全含于 master(0 ahead),历史基点指针 |
| `iridite/vergex_data_fetch` | 无 worktree(仅指针) | ✓ | 已全含于 master(0 ahead),历史指针 |

> 远端另有 **`origin/iridite/add-trade-funnel-audit`**(本地无对应分支,内容已并入 master,31 behind / 0 ahead)。不影响复刻,仅登记。

---

## 2. 进行中的工作:全项目 Bug / SDD 审查 + 修复

- **审查**:全项目覆盖已完成,发现 `#1`–`#180`(9 HIGH / 99 MED / 72 LOW + 9 条已核验推翻)。
- **修复**:在分支 `fix/bug-review-fixes` 上进行。已修 **42 findings / 12 提交**(11 个修复批 + 1 个文档批),**9 个 HIGH 全部处置**,全 SDD spec 套件 **588 passed / 0 failed** + 15 个新回归测试文件。
- **权威记录**(位于 `fix/bug-review-fixes` 分支,未合并 master):
  - 台账 + 权威「Fix Progress」表:`docs/_ai/FULL_BUG_REVIEW_2026-07-06.md`
  - 短交接指南 + Fix Triage:`docs/_ai/BUG_REVIEW_HANDOFF_2026-07-06.md`
  - 修复阶段研发日志:`docs/_ai/DEVLOG.md` 顶部条目(仅该分支版本)
- **已修批次**:B1 密钥防泄漏 / B2 风控闸门(信任边界) / B3 隧道鉴权 / B4a 决策链 / B5 学习闭环 / B6 自愈+运行时 / B10a SDD 注册+契约。
- **剩余批次**(逐批续做):B7 组合·PnL·对账·broker·持久化 · B8 前端诚实性(需 `npm run build`)· B9 数据·分析·doctor·cli · B10 文档漂移尾。
- **暂缓(耦合/基建,各记原因于台账)**:市场类型数据模型迁移(#9/10/11/13/14)、operator 鉴权(#40/41)、#109/#143/#102-pending/#17、运行时性能 #90/#147/#134。
- **⚠️ 合并注意**:`fix/bug-review-fixes` 基于较旧基点(`iridite/bug_fix` HEAD),`origin/master` 自分叉后已推进多个提交(并发会话,含 `c945110f "Scope auto guards to their source symbol"` 可能与 B5 的 guard 改动重叠)。PR / 合并时需 rebase 或 merge master 并解决可能的冲突——本分支上**未做** rebase(留待 PR 阶段)。

---

## 3. 复刻本地布局(全新克隆后)

worktree 结构本身不进 git,但**所有分支已在 origin**,克隆后按下重建即可完全复刻本地布局:

```bash
git clone git@github.com:iridite/aletheia.git "C:/Users/ollama/Projects/aletheia-trading-os"
cd "C:/Users/ollama/Projects/aletheia-trading-os"   # 主工作树 = master

git worktree add "C:/Users/ollama/orca/workspaces/aletheia-trading-os/bug_fix"               fix/bug-review-fixes
git worktree add "C:/Users/ollama/orca/workspaces/aletheia-trading-os/data_source_enhance"   iridite/data_source_enhance
git worktree add "C:/Users/ollama/orca/workspaces/aletheia-trading-os/system_proxy_support"  iridite/system_proxy_support
git worktree add ".claude/worktrees/api-reference-alignment"   docs/api-reference-alignment
git worktree add ".claude/worktrees/trade-sizing-diagnostics"  feat/trade-sizing-diagnostics
```

---

## 4. 已知不可从远端复刻项 / 注意

- **`ref.temp.md`**:主工作树(master)有一个**未跟踪的临时文件**,未提交、不在远端。若重要请另行处理;否则可 `rm` 丢弃。
- **worktree 结构不入 git**:靠 §3 的 `git worktree add` 重建。
- **敏感文件不入库**:`.env` / `.secret.key` / `secrets.enc` 等按 AGENTS.md 从不推送;新环境需另行配置密钥。
