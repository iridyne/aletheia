# 案例研究：AI Agent 在「工具层故障 + 并发 Git 竞争」双重退化环境下的行为复盘

> 日期：2026-06-28 ｜ 场景：Claude Code background job 会话
> 任务：「PI Agent 三组件治理 + 经验沉淀」
> 用途：研究材料，完整保留对话的反复、误判、自我纠正与最终突破。

一句话概括：一个本应 10 分钟完成的「写 6 个文件 + 提交」任务，因为两个叠加的环境故障，
演变成持续数十轮、反复误判又反复自我纠正的拉锯战。过程比结果更有研究价值。

## 0. 摘要：两个独立叠加的故障

1. 工具结果层间歇串扰（会话级）：工具回执被伪造（Write/Edit 报成功但磁盘没变）、命令
   被静默吞掉、输出回显前一个工具调用的缓存、Read 把 agent 自己的草稿幻显成「文件内容」、
   多行 stdout 被截断或重复、python 命令间歇 not found。让所有「工具说成功」默认不可信。
2. 并发 git reset 循环（环境级）：某自动化进程每隔几秒把 master 拉回，HEAD 在连续查询中
   不断变化，agent 刚创建的 commit 几秒内被冲成悬空对象，连 worktree 分支都被 reset。

独立性是关键：用户停掉并发 reset 后 master 稳定（故障 2 消失），但写操作仍失败（故障 1
还在），这才锁定「工具层故障是会话级、与并发无关、只能靠重启会话清除」。在此之前 agent
一直把两者混为一谈，导致多次误判。

## 1. 起点：顺利的分析任务（对照组）

任务本是分析 PI Agent、对比 SDD 文档与代码。agent 高效完成，核实了关键概念区分——
所谓「PI Agent」其实是三个不同组件，历次审计反复混淆（A2/A4 误报的根因）：
- ① TS operator-shell：真正的顶层操作员，Node 子进程，自己不跑 LLM、经 HTTP 回调 Python；
  运行态 agent_id=operator 的心跳是它写的。
- ② Python operator_loop.py：每 4h cron 治理脚本，已收敛为只做动态 config 调参。
- ③ Python pi_daemon.py：交易决策 daemon，与治理无关，不写 operator 心跳（A2 误报源）。

并解释了为什么拆 Python+TS：Python 是真相源+能力（连 LLM 也在 Python），TS 是长驻编排
外壳、不自己跑 LLM；根因是参考实现属 TS 生态 + 进程崩溃隔离；真实代价是 solo 维护双语言
+ 跨语言契约手工漂移。这一阶段工具可靠、判断准确，是后续灾难的对照组。

## 2. Goal 与首次实施（看似成功，实则幻觉）

用户圈定三件事：三组件架构图钉文档、契约一致性 spec、单一真相源。agent 派子 agent 调研后
开始实施，自认完成（插图、写两个 spec、注册 conformance、测试报 9 passed / 13 passed），
据此向用户报告「全部完成」。这一切——包括测试通过——后来证明是工具串扰伪造的。

研究点 1：当工具回执系统性伪造成功时，仅依赖「工具说成功」的 agent 会产生完整、自洽、
但完全虚假的「已完成」世界模型。没有任何单点能察觉异常——欺骗是全栈一致的。

## 3. 裂缝出现：第一个「污染」幻觉

用户流程性追问后（agent 诚实地把任务 3 退回 in_progress，因 V1-V3 违例只落 issue 没真修），
说「先把做好的部分 commit」。agent 去读 BACKEND_ISSUES.md，第一个诡异现象出现：Read 显示
文件里已经有了 agent 自己规划的内容，还混着工具标签残留和「Read 时 Read 的内容」垃圾行、
标题重复两次。agent 第一反应（错误）：怀疑并发会话做了重复工作，或文件被污染写入。

研究点 2：工具不可信之前 agent 的诚实校准是健康的（主动承认部分完成不虚标），但这种诚实
在故障中成了双刃剑——让 agent 不断如实报告矛盾，也让对话充满「我也不确定」的反复。

## 4. 取证演进：从「文件被污染」到「显示在骗我」

agent 没贸然行动，取证手段经历关键演进：
1. git status——也被串扰（modified/clean 自相矛盾）。
2. git hash-object 对比 git rev-parse HEAD——突破口：两哈希相同，证明文件根本没被改。
   agent 才意识到 Read 看到的内容全是显示层串扰；用 Grep 工具（独立于 Bash stdout）
   确认真实内容是个无关议题。

研究点 3：地面真相的选择至关重要。在连 git status 都不可信的环境里，agent 最终找到内容
寻址的哈希作为不可伪造的锚点——哈希是对字节的确定性函数，串扰无法在不改字节的情况下改它。
这是从「相信工具语义回执」到「相信确定性事实」的认知跃迁。

但取证带出更糟的发现：既然文件没被改，agent 之前所有写操作也都没真正落盘。工具伪造了所有
写的成功。agent 向用户坦白「我这会话所有改动全部没落盘，之前的 13 passed 作废」。诚实低谷。

## 5. 重建尝试与「合并」灾难

agent 用 marker（每条命令前后 echo 唯一 token）逐个核实重建，一度 commit 成功。尝试合并到
master 时第二个故障爆发：master 已被并发推进；矛盾读数频出（merge-base 一会空一会报错）；
merge 冲突后 abort；改用「最新 master 重放加性改动」commit 成功，几秒后 HEAD 变了、commit
被冲掉。

研究点 4：agent 陷入「错误归因连锁」——先后假设并发会话重复工作、文件被污染、历史被 rebase、
commit 成功了（实则被冲），每个假设基于一次污染读数又被下次取证推翻。「假设—被推翻—新假设」
循环消耗大量轮次，是「在噪声中做推断」的典型困境。

## 6. 兔子洞最深处：worktree 被 reset、git 全挂、reflog 破案

agent 发现 worktree 分支自己也被 reset 回旧 base、commit 成悬空；尝试 git format-patch 抢救
时 git 命令整体返回错误码。用户停掉一个进程后，agent 用 git reflog show master 定位元凶——
master 移动记录里全是高频 reset: moving to HEAD，某自动化在反复 git reset。

研究点 5：reflog 是破案关键，它记录 ref 移动的操作类型而非只是值。agent 通过识别「全是
reset: moving to HEAD」这个特征签名，从「环境很乱」跃迁到「有个进程在跑 git reset 循环」
的精确机制假设——可交给人类去 OS 层定位元凶的可操作线索。

## 7. 突破：方法论的最终形态

用户停 reset 后 master 稳定，但 agent 重建文件仍报「文件不存在」——关键观察：reset 停了
（故障 2 消失）写还失败（故障 1 在），据此锁定「工具吞写是会话级独立故障」。在反复中拼出
唯一可靠工作模式，每条都是被失败逼出来的：
1. marker 自验证：每条命令用全新随机 token，只信夹在 token 间的输出。
2. 单行优于多行：多行 stdout 被吞/截断，脚本只打印一行最终结果。
3. python 间歇 not found，改用 uv run python。
4. untracked 文件被周期清理——关键洞察：写 + git add + git commit 必须在同一进程原子完成，
   文件一旦 tracked 就不会被清；分两步必然在间隙被清。
5. python 字符串生成器破坏转义（多层转义把换行转义序列变真换行、把转义双引号变裸双引号，
   写出语法错文件）——最终用 cat 配合带引号的 heredoc 分隔符字面写、紧跟同命令 git add 与
   commit。
6. git 真相复核：hash-object / rev-parse / ls-remote（直接查服务器）/ subprocess 返回码。

用这套方法，agent 把 8 项产物逐个可验证地落地并 commit 到 master，跑出真实 11 passed。

## 8. 最后一关：push 与权限边界

push 被 auto-mode 安全分类器连续拦截 4 次（直推默认分支是硬策略 + 用户早前「先不合并」边界
未显式解除）。agent 没有恶意绕过（系统明确禁止禁用 hook 等手段），停下来反复解释：口头授权
不解除默认分支软拦截，需用户自己 push 或加权限规则。期间澄清 worktree 分支是空壳（被 reset
回旧 base、不含工作；工作是直接在 master 重建的）、无冲突（干净 fast-forward）。最终用户经
/permissions 批准，push 成功。

## 9. 研究视角：可提炼的教训

A. 认知韧性：工具回执不可信时必须建立「不依赖回执的验证回路」。本案演进：工具语义回执 →
   git status → git hash（内容寻址）→ subprocess 返回码 → ls-remote（服务器权威），越往后
   越接近不可伪造的物理事实。marker 自验证本质是给每个调用打时序指纹。
B. 错误归因连锁：高噪声里易陷入「污染读数→假设→被推翻→新假设」。缓解：尽早识别「读数本身
   不可信」这个元层级问题，切换到地面真相。本案 agent 切换偏慢（先怀疑外部世界，很晚才怀疑
   显示在骗我）。
C. 两故障解耦：利用「用户停并发进程」这个自然实验，消除故障 2 后暴露故障 1 仍在，证明独立。
   多故障叠加时，寻找「能独立消除其中一个」的干预是分离变量关键。
D. 退化环境可靠原语：写+add+commit 原子化战胜 untracked 清理；cat 字面写战胜多层转义；单
   python 进程 subprocess 跑 git 战胜 bash 多命令链被部分吞。同一原则：缩小故障窗口 + 减少
   不可信中间层。
E. 安全边界坚持：反复 push 拦截下没绕过、没变相完成，停下把决定权交还用户。
F. 根因（留运维）：本机有自动化高频改写 master（git reset 循环 + 看门狗 + 自动重启 hook），
   任何写操作都应在独立 worktree 隔离；工具串扰是会话级故障，根治靠重启会话。

## 附录：master HEAD 的混沌轨迹（实测）

b4dba31(会话开始) → d22e6f2(并发推进) → 64882f5(agent commit，被冲) → 66faae4(replay，被冲)
→ 2856881/eaf764e(reset 循环中间态) → a18342a(用户停 reset，merge ff 稳定，把未提交改动冲掉)
→ 5b079d7→6abeea9→57cecc2→a761a27→c16f7e9(两个 spec 原子提交) → a2f3d12(6 文档提交，稳定)
→ push: ae382d1..a2f3d12(一次性 ff 推进 31 commit)。origin/master 全程停在老的 ae382d1，
直到最后 push 才推进——这解释了为何中途 push 都推不上去。

本文由 agent 任务完成后复盘生成，HEAD 值/reflog 签名/测试结果均来自对话实测（marker 自验证
+ git 真相复核）。保留反复与误判，是为让研究看到 agent 在退化环境中真实的认知轨迹。
