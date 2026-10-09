请按固定流程盘点生命周期能力地图，并把结果作为评论贴到路线图 issue 上。

依据：`docs/design/proposals/lifecycle_capability_map.md`（accepted）。能力定义只来自
`docs/product/CAPABILITY_MAP.md`；当前级别只出现在本次评论里，**不改任何仓库文件**。

必须执行：

1. 读 `docs/product/CAPABILITY_MAP.md`：能力编号、目标级别、探针、L0–L4 判据。读 `AGENTS.md` 的安全边界。
2. 找到路线图 issue（标题以 `[roadmap] Lifecycle capability map` 开头），读上一次盘点评论，作为对比基线。
3. 确认基线：`master@origin` 的提交号；Fedora 运行版本（`ssh u7p 'cd ~/aletheia && uv run --no-sync aletheia control status --json'` 里的 `runtime_session.boot_sha`、`session_id`、`started_at`、`schema_version`、`execution_mode`）。两者代码不一致时，分别写明。
4. 逐项查代码与测试（E1/E2）：每个能力的代码符号、生产调用方是否仍存在且接通，聚焦测试是否仍断言最终结果。只读代码，不跑全量测试；需要时只跑该能力的聚焦测试。探针失效（符号改名、调用方消失）要单独报"探针漂移"。
5. 逐项查 Fedora 只读证据（E3）：数据库一律用只读 URI 打开（`sqlite3.connect("file:/home/admin/.aletheia/app.db?mode=ro", uri=True)`），日志只读。每个数字写明查询时间和统计窗口；不同窗口的数字不相加、不比较。不重启、不部署、不改配置、不读也不打印任何凭证。
6. 查相关 issue：`gh issue list --label cap/CAP-XXX-nn --state open`，以及 `priority/now`、`wip/*` 中与该能力有关的 issue。引用前核实 issue 仍是 open，PR 不当 issue 引用。
7. 按 L0–L4 判据给出每项能力的当前级别：取判据完全满足的最高一级。L4 只能写"owner 已接受"或"未接受"，AI 不得自行判定 L4。在部署版本上观察到失败时，报仍然成立的最高级别，并把失败列为缺口。
8. 输出一条评论（中文、说人话，每个术语第一次出现就解释），结构固定：
   - 基线（代码提交、运行版本、查询时间）；
   - 每项能力一行：编号、目标、当前级别、关键证据（带数字和窗口）、对应 issue、与上次相比的变化；
   - 缺口清单：严重度（高/中/低）、对应 issue 或"无 issue"、D0/D1/D2 建议；
   - 探针漂移；
   - "给 owner 的建议候选"：排序规则是越靠因果链上游、离目标级别越远的越靠前。明确写"这是给 owner 的建议，不是工作队列"。
9. 用 `gh issue comment <路线图 issue 号> --body-file <文件>` 贴评论。

不要做的事：

- 不改 `CAPABILITY_MAP.md` 或任何仓库文件；登记表的增删改由 owner 决定，走 PR。
- 不新增、不移除任何 `priority/*` 标签；不批量新建 issue。没有 issue 的新缺口只列在评论里。
- 不把盘点结论当作开工授权，不自选积压工作（AGENTS.md 有界委托规则不变）。
