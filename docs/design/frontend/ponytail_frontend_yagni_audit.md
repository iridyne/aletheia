# Aletheia 前端代码库 Ponytail YAGNI 审计报告

> **审计工具**：Ponytail Audit Mode（极简主义全库防过度工程化扫描）  
> **核心原则**：如无必要，勿增实体（YAGNI）。能用平台原生特性的绝不手写，能内联的绝不单拉文件，有 1 个调用的绝不抽象多层，死代码直接删除。

---

## 排名清单（按可削减代码量与复杂度从大到小排序）

1. `delete: SetupBanner.tsx (48 行)`。全站 0 处引用调用，遗留下来的僵尸脚手架。替换方案：直接删除。`[frontend/src/components/SetupBanner.tsx]`
2. `delete: ObjectDetail.tsx (55 行)`。全站 0 处引用调用，早期设想的通用 Schema 对象渲染器。替换方案：直接删除。`[frontend/src/components/ObjectDetail.tsx]`
3. `yagni: ProposalRationaleDetail.tsx (88 行) 与 ConvictionMeter.tsx (18 行)`。与 `PositionDecisionSummary.tsx (85 行)` 逻辑 90% 重复，都在用 `parseDecisionRationale` 解析 `{thesis}\n---\n{json_meta}`。替换方案：合并为一个统一的 `<DecisionRationaleView />`。`[frontend/src/components/]`
4. `yagni: deferred.ts (35 行)`。写了 35 行复杂的 `requestIdleCallback` + `visibilitychange` 调度器，全站仅在 `App.tsx:123` 有 1 处调用（用来把更新检查延后 3 秒）。替换方案：内联原生的 `setTimeout(..., 3000)`，3 行搞定。`[frontend/src/lib/deferred.ts]`
5. `yagni: MeasuredPathSelector.tsx (30 行) 与专属测试 (50 行)`。专门拉了一个组件文件和一个测试文件，仅仅为了渲染一个包含 1 个 `<select>` 的下拉框，全站仅在 `Learning.tsx:210` 调用一次。替换方案：直接内联回 `Learning.tsx`。`[frontend/src/components/MeasuredPathSelector.tsx]`
6. `yagni: GuardAuthorityNotice.tsx (11 行) 与专属测试 (45 行)`。专门拉了一个独立组件文件和测试文件，仅仅为了渲染 3 段静态说明文字，全站仅在 `AgentModelsTab.tsx:181` 调用一次。替换方案：直接内联回 `AgentModelsTab.tsx`。`[frontend/src/components/GuardAuthorityNotice.tsx]`
7. `native: Charts.tsx:253 负坐标隐藏 (-9999px) 菜单`。为了隐藏时间周期下拉菜单写了负 9999 像素。替换方案：React 原生条件渲染 `{open && <TimeframeMenu />}`。`[frontend/src/pages/Charts.tsx]`
8. `native: Settings 各 Tab 散落 80 余处静态 style={{ width: '80px' }}`。行内手动分配对象增加 GC 负担。替换方案：直接使用已有 Tailwind v4 原子类（`w-20`、`w-28`、`mb-3`）。`[frontend/src/features/settings/tabs/]`
9. `delete: Settings 各 Tab 中 35 处 as any 强转`。工程已有自动生成的 `settings.d.ts`，却仍手动逃避类型检查。替换方案：声明 `settings: SettingsResponse`，删除全部 `as any`。`[frontend/src/features/settings/tabs/]`
10. `shrink: TradesPositions.tsx 存量持仓 12 列过度碎片化`。将 Symbol、Exchange、Market 拆 3 列，Stop 与 TP 拆 2 列。替换方案：合并为 6 组复合列，减少约 80 行表格样板代码。`[frontend/src/pages/TradesPositions.tsx]`
11. `shrink: PredictionLens.tsx 与 SerenityLens.tsx 双绝对时间列`。将 `Started` 与 `Completed` 拆为两列 20 字符时间。替换方案：合并为 `Started (Duration)`，减少 2 列重复定义。`[frontend/src/pages/]`

---

## 削减收益总结

* **净削减代码量**：预计净减少 **-450+ 行冗余代码**
* **消除冗余文件**：可直接删除/内联合并 **4 个多余组件文件及其 3 个过度测试文件**
* **消除外部依赖**：0 新增依赖，全面利用标准库与现有 Tailwind 原子类
