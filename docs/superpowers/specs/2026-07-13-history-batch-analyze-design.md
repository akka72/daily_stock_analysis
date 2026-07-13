# StockBar「批量分析」按钮 — 设计稿

- **日期**: 2026-07-13
- **状态**: 已通过口头设计评审（目标修正为 StockBar），待用户复核 spec
- **范围**: 纯前端（WebUI），后端零改动

> **修订记录**: 初版以「历史报告列表 HistoryList」为目标，但深挖发现 `HistoryList` 组件及其 store 选择机制（`historyItems`/`selectedHistoryIds`/`toggleSelectAllVisible`/`deleteSelectedHistory`）**未被任何页面渲染，是孤立死代码**。HomePage 实际渲染的是 `<StockBar>`（`HomePage.tsx:666`），且 StockBar **已自带**多选+全选+删除（`StockBar.tsx:36-74`）。故目标改为 StockBar。设计实质不变（在删除按钮旁加批量分析按钮 + store 批量入队 action），唯一结构差异：选择态在 StockBar 本地 `useState`，store action 接收 `stockCodes` 参数。

---

## 1. 背景与动机

用户希望「把选中的股票批量加入待分析队列」。现状调研结论：

- **后端完全支持批量**：`POST /api/v1/analysis/analyze` 带 `stock_codes:[...]` + `async_mode:true` → `_handle_async_analysis_batch`（`api/v1/endpoints/analysis.py:350`）→ `task_queue.submit_tasks_batch`。多只返回 202 `BatchTaskAcceptedResponse`，自动去重（跳过正在分析的，返回 `duplicates`），`MAX_BATCH_SIZE=50`。
- **前端 API wrapper 已支持** `stock_codes` 透传（`analysisApi.analyzeAsync`，`api/analysis.ts:60`）。
- **前端类型已存在**：`BatchTaskAcceptedResponse` / `BatchTaskAcceptedItem` / `BatchDuplicateTaskItem`（`types/analysis.ts:328-347`），`AnalyzeAsyncResponse = TaskAccepted | BatchTaskAcceptedResponse`。
- **StockBar 已自带选择 UI**：`StockBar.tsx` 内 `selectedCodes: Set<string>`（line 36）、`toggleCode`、`toggleSelectAll`、`handleDeleteSelected`、select-all 复选框、每行复选框、「删除」按钮、selectedCount 徽标——均由 `onDeleteStock` prop 触发显示，HomePage（line 672）已传 `handleDeleteStock`。**选择按 stockCode（字符串）键控，每条即一只独立股票，无需去重。**
- **缺口**：UI 上没有「批量入队分析」入口；`submitAnalysis` 只支持单只。

本次在 StockBar 删除按钮旁加「批量分析」按钮，复用其现成选择 UI。

## 2. 目标 / 非目标

**目标**
- 在 StockBar 选中 ≥1 只后，一键把选中股票批量加入待分析队列。
- 复用 StockBar 现成选择 UI、SSE 任务流（TaskPanel 自动展示新任务）、后端去重语义。
- 组件保持职责清晰、后端零改动。

**非目标（YAGNI）**
- 不做提交前确认弹窗（selectedCount 徽标已显示数量）。
- 不加 `force_refresh`（新任务自然生成新报告）。
- 不动 HistoryList 死代码（不在本次范围；如需清理另行处理）。
- 不碰 DecisionSignalsPage / 盯盘列表。
- 不改后端。

## 3. 数据流

```
用户在 StockBar 勾选个股 → selectedCodes (StockBar 本地 Set<string>)（已有）
  → 点「批量分析」按钮
  → StockBar.handleAnalyzeSelected()
      · codes = [...selectedCodes].filter(c => c && c !== 'MARKET')  // 排除大盘复盘伪项
      · onAnalyzeSelected(codes)       // 透传给 HomePage → store
      · setSelectedCodes(new Set())    // 立即清空本地勾选
  → store.submitBatchAnalysis(codes)
      1. 过滤空/'MARKET'，按 normalizeStockCode 去重（防御性，StockBar 已按股去重）
      2. 为空 → set inputError「请选择有效的股票（不含大盘复盘）」并返回
      3. > 50 → set error「一次最多批量分析 50 只，当前选中 N 只」并返回
      4. set { isAnalyzingBatch:true, error:null, inputError:undefined, duplicateError:null }
      5. await analysisApi.analyzeBatch(codes, { notify: state.notify })
         · 202 → 解析 { accepted, duplicates, message }，拼 batchSummary
         · 409（仅单股+重复）→ 抛 DuplicateTaskError → 走 duplicateError
      6. set batchSummary（5s 后自动清空，模块级定时器）
      7. finally: isAnalyzingBatch:false
  → SSE 任务流（useTaskStream，已有）自动把 accepted 任务推进 TaskPanel
  → refreshActiveTasks / refreshStockBar 既有 30s 轮询自然刷新
```

## 4. 改动文件清单

### 4.1 `apps/dsa-web/src/api/analysis.ts` — 新增 `analyzeBatch`

薄封装，复用现有端点与类型，**无新增 type**：

```typescript
/**
 * 批量异步分析。多只 → 202 BatchTaskAcceptedResponse；
 * 单只+重复 → 抛 DuplicateTaskError（兼容现有 409 语义）。
 */
async analyzeBatch(
  stockCodes: string[],
  options?: { notify?: boolean; reportLanguage?: ReportLanguage; skills?: string[] }
): Promise<BatchTaskAcceptedResponse> {
  const requestData = {
    stock_codes: stockCodes,
    report_type: 'detailed',
    async_mode: true,
    analysis_phase: 'auto',
    selection_source: 'manual',
    ...(options?.notify !== undefined && { notify: options.notify }),
    report_language: options?.reportLanguage,
    skills: options?.skills,
  };
  const response = await apiClient.post<Record<string, unknown>>(
    '/api/v1/analysis/analyze',
    requestData,
    { validateStatus: (s) => s === 200 || s === 202 || s === 409 }
  );
  if (response.status === 409) {
    const e = toCamelCase<{ stockCode: string; existingTaskId: string; message: string }>(response.data);
    throw new DuplicateTaskError(e.stockCode, e.existingTaskId, e.message);
  }
  const data = toCamelCase<TaskAccepted | BatchTaskAcceptedResponse>(response.data);
  // 单只成功 → 后端返回 TaskAccepted；归一化为 1 元素 accepted 数组，便于上层统一处理。
  if ('taskId' in data && !('accepted' in data)) {
    return { accepted: [{ ...data }], duplicates: [], message: data.message ?? '' };
  }
  return data as BatchTaskAcceptedResponse;
}
```

- 按需补 `BatchTaskAcceptedResponse`、`TaskAccepted`、`ReportLanguage` 的 type import。
- 不改 `analyze` / `analyzeAsync`。

### 4.2 `apps/dsa-web/src/stores/stockPoolStore.ts` — 新增 state + action

**State**（`StockPoolState` interface + `initialState`）：
- `isAnalyzingBatch: boolean`（初始 `false`）
- `batchSummary: string | null`（初始 `null`）

**Action `submitBatchAnalysis(stockCodes: string[])`**（接在 `submitAnalysis` 之后；注意参数化——选择态在 StockBar 本地，不在 store）：
- 收集/过滤/校验逻辑见 §3。
- 成功后 `batchSummary` 文案：
  - `duplicates.length === 0` → `已加入 {accepted.length} 只到分析队列`
  - `duplicates.length > 0` → `已加入 {accepted.length} 只，{duplicates.length} 只正在分析中已跳过`
  - `accepted.length === 0 && duplicates.length > 0` → `选中的 {duplicates.length} 只均在分析中，已全部跳过`
- 自动清空：模块级 `batchSummaryTimerId`（仿 `dismissedTaskIds` 模式），`setTimeout(() => set({ batchSummary: null }), 5000)`；新一次提交先 `clearTimeout` 旧定时器，避免竞态覆盖。
- 异常：`DuplicateTaskError` → `duplicateError`；其余 → `error: getParsedApiError(e)`。
- `resetDashboardState`：`clearTimeout(batchSummaryTimerId)`，并把两字段纳入 `initialState` 重置。

不动 `submitAnalysis` / 现有任何字段。**不在 store 增加 selection 态**（保留在 StockBar 本地）。

### 4.3 `apps/dsa-web/src/hooks/useHomeDashboardState.ts` — 转发 3 个字段

在 `useShallow` 选择器内（`deleteSelectedHistory` 之后或 `submitAnalysis` 附近）追加：
```typescript
isAnalyzingBatch: state.isAnalyzingBatch,
batchSummary: state.batchSummary,
submitBatchAnalysis: state.submitBatchAnalysis,
```

### 4.4 `apps/dsa-web/src/components/history/StockBar.tsx` — 加按钮 + 摘要 + handler

**Props 扩展**（`StockBarProps`）：
```typescript
onAnalyzeSelected?: (stockCodes: string[]) => void;
isAnalyzingBatch?: boolean;
batchSummary?: string | null;
```

**新增 handler**（组件内，紧邻 `handleDeleteSelected`）：
```typescript
const handleAnalyzeSelected = useCallback(() => {
  if (!onAnalyzeSelected || selectedCodes.size === 0 || isDeleting || isAnalyzingBatch) return;
  const codes = [...selectedCodes].filter((c) => c && c !== 'MARKET');
  if (codes.length === 0) return;
  onAnalyzeSelected(codes);
  setSelectedCodes(new Set());
}, [onAnalyzeSelected, selectedCodes, isDeleting, isAnalyzingBatch]);
```

**工具栏**（现有 `flex items-center gap-2` 行，删除按钮之后追加，仅当 `onAnalyzeSelected` 提供时渲染）：
```tsx
{onAnalyzeSelected && (
  <Button
    variant="primary"
    size="xsm"
    onClick={() => void handleAnalyzeSelected()}
    disabled={selectedCount === 0 || isDeleting || isAnalyzingBatch}
    isLoading={isAnalyzingBatch}
    className="stock-bar-batch-analyze-button"
  >
    {isAnalyzingBatch ? t('home.analyzing') : t('common.batchAnalyze')}
  </Button>
)}
```
> `variant="primary"`：`Button` 无 `primary-subtle`，主操作用 solid primary，与 `danger-subtle` 删除按钮形成主次对比。

**摘要**（工具栏行下方，`items.length > 0 && onDeleteStock` 块内）：
```tsx
{batchSummary && (
  <div className="text-[11px] text-info px-2 animate-in fade-in duration-200">
    {batchSummary}
  </div>
)}
```

组件不直接访问 store（保持通过 props 由 HomePage 驱动）。

### 4.5 `apps/dsa-web/src/pages/HomePage.tsx` — 透传 props（`<StockBar>` 在 line 666）

从 `useHomeDashboardState` 解构出 `submitBatchAnalysis` / `isAnalyzingBatch` / `batchSummary`，追加到 `<StockBar>`：
```tsx
<StockBar
  items={mergedStockBarItems}
  isLoading={isLoadingStockBar}
  selectedStockCode={selectedReport?.meta.stockCode}
  selectedRecordId={selectedReport?.meta.id}
  onItemClick={handleHistoryItemClick}
  onDeleteStock={handleDeleteStock}
  isDeleting={isDeletingStock}
  onAnalyzeSelected={submitBatchAnalysis}
  isAnalyzingBatch={isAnalyzingBatch}
  batchSummary={batchSummary}
  className="flex-1 overflow-hidden"
/>
```
并把三者纳入 `sidebarContent` 的 `useMemo` 依赖数组。

### 4.6 `apps/dsa-web/src/i18n/uiText.ts` — 新增 1 个 i18n key

**仅新增** `common.batchAnalyze`（zh「批量分析」/ en「Analyze」），插入位置：zh 在 `common.deleting`（line 9）之后；en 在 `common.deleting`（line 809）之后。loading 态**复用既有** `home.analyzing`（zh「分析中」line 152 / en「Analyzing」line 952）。

> `batchSummary` / 超限 / 空选提示文案在 store 里以中文字符串拼装（含动态数字），暂不走 i18n（低收益权衡）。

## 5. 错误处理矩阵

| 场景 | 后端 | 前端处理 | 用户可见 |
|---|---|---|---|
| 只选了 MARKET（大盘复盘） | — | StockBar 过滤后 codes 为空，按钮不触发（handler return） | 无（按钮看似可点但选中无有效股；store 侧再兜底 inputError） |
| 选中含 MARKET + 普通股 | — | StockBar 过滤掉 MARKET，只传普通股 | 正常入队 |
| 去重后 > 50 | — | 前端先拦 → `error` | 横幅「一次最多批量分析 50 只，当前选中 N 只」 |
| 正常入队 | 202 | 拼 `batchSummary` | StockBar 工具栏下「已加入 N 只…」5s |
| 部分重复 | 202 + duplicates | `batchSummary` 含跳过数 | 「已加入 N 只，M 只…已跳过」 |
| 单只且重复 | 409 | `DuplicateTaskError` → `duplicateError` | 行内「股票 XXX 正在分析中…」 |
| 全部重复（多只） | 202 accepted=[] | `batchSummary` 全跳过文案 | 「选中的 N 只均在分析中…」 |
| 网络/5xx | — | `error: getParsedApiError` | 现有错误横幅 |
| 后端 400（>50 漏网） | 400 | `getParsedApiError` | 现有错误横幅 |

## 6. 测试

**store（扩展 `apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts`）** — mock `analysisApi.analyzeBatch`：
1. 传入含 'MARKET' 与空串 → 过滤后只把有效 code 传给 `analyzeBatch`。
2. 过滤后为空 → 不调 `analyzeBatch`，置 `inputError`。
3. 有效 code > 50 → 不调 `analyzeBatch`，置 `error`。
4. 202 + accepted=3, duplicates=1 → `batchSummary` 含「已加入 3 只，1 只…已跳过」，`isAnalyzingBatch` 复位。
5. 202 + accepted=0, duplicates=2 → 全跳过文案。
6. `analyzeBatch` 抛 `DuplicateTaskError` → `duplicateError` 置位。
7. `analyzeBatch` 抛普通错 → `error` 置位。
8. `resetDashboardState` → `isAnalyzingBatch=false`, `batchSummary=null`, 定时器清除。

**StockBar（新建 `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx`）** — 仿 `HistoryList.test.tsx` 模式（vitest + testing-library）：
9. `selectedCount === 0` → 批量分析按钮 disabled。
10. 勾选 2 只（含 1 只 MARKET）+ 点按钮 → `onAnalyzeSelected` 被调用且参数不含 'MARKET'，本地勾选清空。
11. `isAnalyzingBatch=true` → 按钮 `isLoading` 且 disabled。
12. 传 `batchSummary` → 渲染该文案；不传 → 不渲染。
13. 未传 `onAnalyzeSelected` → 不渲染批量分析按钮（向后兼容）。

**后端**：无改动，`submit_tasks_batch` 既有覆盖足够。

## 7. 风险与权衡

- **选择态在 StockBar 本地、不入 store**：与现有删除选择一致（StockBar 已用本地 `selectedCodes`），最小改动、不污染 store。代价：批量分析进行中若 StockBar 卸载则本地勾选丢失——可接受（HomePage 常驻）。
- **MARKET 伪项过滤**：在 StockBar（透传前）与 store（兜底）双重过滤，确保不把 'MARKET' 当股票提交。
- **单股归一化**：`analyzeBatch` 把单只 `TaskAccepted` 包成 1 元素数组，避免上层 union 判别。
- **与删除互斥**：两按钮共享 `isDeleting`/`isAnalyzingBatch` 的 disabled 条件。
- **Button variant**：用 `primary`（无 `primary-subtle`）。

## 8. 验收清单

- [ ] StockBar 选中 1~50 只普通股 → 一键入队，TaskPanel 出现新任务，`batchSummary` 显示。
- [ ] 含 MARKET 项 → 提交时自动排除。
- [ ] 含正在分析的股 → summary 显示跳过数，不报错。
- [ ] 单只选中且该股在分析中 → 行内 duplicate 提示（非横幅）。
- [ ] 选 >50 → 横幅拦截，不发请求。
- [ ] 删除进行中 → 批量分析按钮禁用；反之亦然。
- [ ] 全部既有前端测试 + 新增用例通过。

## 9. 改动文件总览

| 文件 | 性质 |
|---|---|
| `apps/dsa-web/src/api/analysis.ts` | 新增 `analyzeBatch` 方法 |
| `apps/dsa-web/src/stores/stockPoolStore.ts` | 新增 2 state + 1 action(`submitBatchAnalysis`) + reset |
| `apps/dsa-web/src/hooks/useHomeDashboardState.ts` | 转发 3 个字段 |
| `apps/dsa-web/src/components/history/StockBar.tsx` | 新增 3 props + 1 按钮 + 摘要渲染 + handler |
| `apps/dsa-web/src/pages/HomePage.tsx` | `<StockBar>` 透传 3 props + useMemo 依赖 |
| `apps/dsa-web/src/i18n/uiText.ts` | 新增 `common.batchAnalyze`（复用 `home.analyzing`） |
| `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx` | **新建** |
| `apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts` | 扩用例 |

**后端零改动。无新增 type（复用既有 `BatchTaskAcceptedResponse` 等）。**
