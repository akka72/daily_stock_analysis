# StockBar「批量分析」按钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 HomePage 的 StockBar（顶部股票栏）选中 ≥1 只股票后，一键批量加入待分析队列，复用其现成的多选/全选 UI 与后端批量去重语义。

**Architecture:** 纯前端，后端零改动。新增 `analysisApi.analyzeBatch` 薄封装（复用既有 `BatchTaskAcceptedResponse` 类型，无新增 type）→ store 新增 `submitBatchAnalysis(stockCodes)` action（选择态保留在 StockBar 本地 `useState`，action 接收 codes 参数）→ `useHomeDashboardState` 转发 3 个字段 → StockBar 在删除按钮旁加「批量分析」`primary` 按钮 + 摘要渲染 → HomePage 透传 props。`MARKET`（大盘复盘）伪项在 StockBar 与 store 双重过滤。

**Tech Stack:** React 18 + TypeScript + Zustand + vitest + @testing-library/react。i18n 单文件 `uiText.ts`。组件 Button variant 限定集合（无 `primary-subtle`，用 `primary`）。

**Spec:** `docs/superpowers/specs/2026-07-13-history-batch-analyze-design.md`

**运行约定：** 前端命令在 `apps/dsa-web/` 下执行；测试 `npx vitest run <path>`，类型检查 `npx tsc --noEmit`。提交不加 Co-Authored-By（全局已禁用）。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `apps/dsa-web/src/api/analysis.ts` | 后端 HTTP 封装 | 新增 `analyzeBatch` 方法 |
| `apps/dsa-web/src/api/__tests__/analysis.test.ts` | api 单测（**新建**） | analyzeBatch 的 202/409/归一化 |
| `apps/dsa-web/src/stores/stockPoolStore.ts` | 仪表盘业务态（Zustand） | 新增 `isAnalyzingBatch`/`batchSummary` state + `submitBatchAnalysis` action + reset |
| `apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts` | store 单测 | 扩 `submitBatchAnalysis` 用例 |
| `apps/dsa-web/src/hooks/useHomeDashboardState.ts` | store→HomePage 选择器 | 转发 3 字段 |
| `apps/dsa-web/src/components/history/StockBar.tsx` | 顶部股票栏（已有多选/全选/删除） | 加按钮 + 摘要 + 3 props + handler |
| `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx` | StockBar 单测（**新建**） | 批量分析按钮行为 |
| `apps/dsa-web/src/pages/HomePage.tsx` | 首页 | 解构 + 透传 props + useMemo 依赖 |
| `apps/dsa-web/src/i18n/uiText.ts` | 文案 | 新增 `common.batchAnalyze`（复用 `home.analyzing`） |

后端零改动；`BatchTaskAcceptedResponse` / `TaskAccepted` / `DuplicateTaskError` 类型均已存在。

---

## Task 1: `analyzeBatch` API 方法

**Files:**
- Modify: `apps/dsa-web/src/api/analysis.ts`
- Test: `apps/dsa-web/src/api/__tests__/analysis.test.ts`（新建）

- [ ] **Step 1: 写失败测试（新建测试文件）**

创建 `apps/dsa-web/src/api/__tests__/analysis.test.ts`：

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock 底层 apiClient，使 analyzeBatch 的 HTTP 路径可控。
vi.mock('../index', () => ({
  default: { post: vi.fn() },
}));

import apiClient from '../index';
import { analysisApi, DuplicateTaskError } from '../analysis';

describe('analysisApi.analyzeBatch', () => {
  const post = apiClient.post as unknown as ReturnType<typeof vi.fn>;

  beforeEach(() => {
    post.mockReset();
  });

  it('多只 → 202 BatchTaskAcceptedResponse，透传 accepted/duplicates', async () => {
    post.mockResolvedValueOnce({
      status: 202,
      data: { accepted: [{ task_id: 't1' }, { task_id: 't2' }], duplicates: [], message: 'ok' },
    });
    const result = await analysisApi.analyzeBatch(['600519', '000001']);
    expect(result.accepted).toHaveLength(2);
    expect(result.duplicates).toEqual([]);
    expect(post).toHaveBeenCalledWith(
      '/api/v1/analysis/analyze',
      expect.objectContaining({ stock_codes: ['600519', '000001'], async_mode: true }),
      expect.anything(),
    );
  });

  it('单只成功 → 后端返回 flat TaskAccepted，归一化为 1 元素 accepted', async () => {
    post.mockResolvedValueOnce({
      status: 202,
      data: { task_id: 't1', stock_code: '600519', message: 'ok' },
    });
    const result = await analysisApi.analyzeBatch(['600519']);
    expect(result.accepted).toHaveLength(1);
    expect(result.accepted[0].taskId).toBe('t1');
    expect(result.duplicates).toEqual([]);
  });

  it('单只重复 → 409 抛 DuplicateTaskError', async () => {
    post.mockResolvedValueOnce({
      status: 409,
      data: { stock_code: '600519', existing_task_id: 't1', message: 'dup' },
    });
    await expect(analysisApi.analyzeBatch(['600519'])).rejects.toBeInstanceOf(DuplicateTaskError);
  });

  it('请求体默认 report_type=detailed、async_mode=true', async () => {
    post.mockResolvedValueOnce({ status: 202, data: { accepted: [], duplicates: [], message: '' } });
    await analysisApi.analyzeBatch(['600519']);
    const body = post.mock.calls[0][1] as Record<string, unknown>;
    expect(body.report_type).toBe('detailed');
    expect(body.async_mode).toBe(true);
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run（在 `apps/dsa-web/` 下）: `npx vitest run src/api/__tests__/analysis.test.ts`
Expected: FAIL — `analysisApi.analyzeBatch is not a function`。

- [ ] **Step 3: 实现 `analyzeBatch`**

在 `apps/dsa-web/src/api/analysis.ts` 顶部 import 块（line 3-13）追加两个类型：

```typescript
import type {
  AnalysisRequest,
  AnalysisResult,
  AnalyzeResponse,
  AnalyzeAsyncResponse,
  AnalysisReport,
  BatchTaskAcceptedResponse,
  MarketReviewAccepted,
  MarketReviewRequest,
  TaskAccepted,
  TaskStatus,
  TaskListResponse,
} from '../types/analysis';
```

在 `analysisApi` 对象内、`analyzeAsync`（line 60-97）之后插入新方法：

```typescript
  /**
   * 批量异步分析。多只 → 202 BatchTaskAcceptedResponse（后端自动去重，返回 duplicates）；
   * 单只且正在分析 → 409 → 抛 DuplicateTaskError；
   * 单只成功（后端返回 flat TaskAccepted）归一化为 1 元素 accepted 数组，便于上层统一处理。
   * @param stockCodes 股票代码数组（调用方需先过滤 'MARKET' 等伪项）
   * @param options notify / reportLanguage / skills，复用 AnalysisRequest 字段
   */
  analyzeBatch: async (
    stockCodes: string[],
    options?: Pick<AnalysisRequest, 'notify' | 'reportLanguage' | 'skills'>,
  ): Promise<BatchTaskAcceptedResponse> => {
    const requestData = {
      stock_codes: stockCodes,
      report_type: 'detailed',
      async_mode: true,
      analysis_phase: 'auto',
      selection_source: 'manual',
      report_language: options?.reportLanguage,
      skills: options?.skills,
      ...(options?.notify !== undefined && { notify: options.notify }),
    };

    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/analysis/analyze',
      requestData,
      { validateStatus: (status) => status === 200 || status === 202 || status === 409 },
    );

    if (response.status === 409) {
      const errorData = toCamelCase<{ stockCode: string; existingTaskId: string; message: string }>(
        response.data,
      );
      throw new DuplicateTaskError(errorData.stockCode, errorData.existingTaskId, errorData.message);
    }

    const data = toCamelCase<TaskAccepted | BatchTaskAcceptedResponse>(response.data);
    // 单只成功 → 后端返回 flat TaskAccepted；包成 1 元素 accepted，避免上层 union 判别。
    if ('taskId' in data && !('accepted' in data)) {
      const single = data as TaskAccepted;
      return { accepted: [{ ...single }], duplicates: [], message: single.message ?? '' };
    }
    return data as BatchTaskAcceptedResponse;
  },
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `npx vitest run src/api/__tests__/analysis.test.ts`
Expected: PASS（4 个用例全绿）。

- [ ] **Step 5: 类型检查 + 提交**

Run: `npx tsc --noEmit`（在 `apps/dsa-web/` 下）— 无报错。

```bash
git add apps/dsa-web/src/api/analysis.ts apps/dsa-web/src/api/__tests__/analysis.test.ts
git commit -m "feat(web): add analysisApi.analyzeBatch wrapper for batch enqueue"
```

---

## Task 2: store `submitBatchAnalysis` action + state

**Files:**
- Modify: `apps/dsa-web/src/stores/stockPoolStore.ts`
- Test: `apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts`

- [ ] **Step 1: 写失败测试（追加到现有 store 测试文件）**

在 `apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts` 顶部确保有这些 import（若已有则跳过）：

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { analysisApi, DuplicateTaskError } from '../../api/analysis';
import { useStockPoolStore } from '../stockPoolStore';

// 对真实 analysisApi 对象打 spy，保留 DuplicateTaskError 真实类。
const analyzeBatchSpy = vi.spyOn(analysisApi, 'analyzeBatch');
```

在文件末尾追加 `describe('submitBatchAnalysis', ...)`：

```typescript
describe('submitBatchAnalysis', () => {
  beforeEach(() => {
    analyzeBatchSpy.mockReset();
    useStockPoolStore.getState().resetDashboardState();
  });

  it('过滤 MARKET/空串后只提交有效股并拼成功摘要', async () => {
    analyzeBatchSpy.mockResolvedValueOnce({
      accepted: [{ taskId: 't1' }, { taskId: 't2' }],
      duplicates: [],
      message: '',
    });
    await useStockPoolStore.getState().submitBatchAnalysis(['600519', 'MARKET', '', '000001']);
    expect(analyzeBatchSpy).toHaveBeenCalledWith(['600519', '000001'], expect.anything());
    const s = useStockPoolStore.getState();
    expect(s.batchSummary).toContain('已加入 2 只');
    expect(s.isAnalyzingBatch).toBe(false);
  });

  it('过滤后为空 → 置 inputError 且不调用 analyzeBatch', async () => {
    await useStockPoolStore.getState().submitBatchAnalysis(['MARKET', '']);
    expect(analyzeBatchSpy).not.toHaveBeenCalled();
    expect(useStockPoolStore.getState().inputError).toBeTruthy();
  });

  it('有效股超过 50 → 置 error 且不调用 analyzeBatch', async () => {
    const codes = Array.from({ length: 51 }, (_, i) => String(600000 + i));
    await useStockPoolStore.getState().submitBatchAnalysis(codes);
    expect(analyzeBatchSpy).not.toHaveBeenCalled();
    expect(useStockPoolStore.getState().error).toContain('50');
  });

  it('部分重复 → 摘要含跳过数', async () => {
    analyzeBatchSpy.mockResolvedValueOnce({
      accepted: [{ taskId: 't1' }],
      duplicates: [{ stockCode: '000001' }],
      message: '',
    });
    await useStockPoolStore.getState().submitBatchAnalysis(['600519', '000001']);
    expect(useStockPoolStore.getState().batchSummary).toContain('1 只正在分析中已跳过');
  });

  it('全部重复 → 全部跳过文案', async () => {
    analyzeBatchSpy.mockResolvedValueOnce({
      accepted: [],
      duplicates: [{ stockCode: '600519' }, { stockCode: '000001' }],
      message: '',
    });
    await useStockPoolStore.getState().submitBatchAnalysis(['600519', '000001']);
    expect(useStockPoolStore.getState().batchSummary).toContain('均在分析中');
  });

  it('抛 DuplicateTaskError → 置 duplicateError 并复位 isAnalyzingBatch', async () => {
    analyzeBatchSpy.mockRejectedValueOnce(new DuplicateTaskError('600519', 't1', 'dup'));
    await useStockPoolStore.getState().submitBatchAnalysis(['600519']);
    const s = useStockPoolStore.getState();
    expect(s.duplicateError).toBe('600519');
    expect(s.isAnalyzingBatch).toBe(false);
  });

  it('抛普通错误 → 置 error', async () => {
    analyzeBatchSpy.mockRejectedValueOnce(new Error('boom'));
    await useStockPoolStore.getState().submitBatchAnalysis(['600519']);
    expect(useStockPoolStore.getState().error).toBeTruthy();
    expect(useStockPoolStore.getState().isAnalyzingBatch).toBe(false);
  });

  it('提交期间 isAnalyzingBatch 为 true，完成后复位', async () => {
    let resolve!: (v: unknown) => void;
    analyzeBatchSpy.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
    const p = useStockPoolStore.getState().submitBatchAnalysis(['600519']);
    expect(useStockPoolStore.getState().isAnalyzingBatch).toBe(true);
    resolve({ accepted: [{ taskId: 't1' }], duplicates: [], message: '' });
    await p;
    expect(useStockPoolStore.getState().isAnalyzingBatch).toBe(false);
  });

  it('resetDashboardState 清空批量分析状态', async () => {
    analyzeBatchSpy.mockResolvedValueOnce({ accepted: [{ taskId: 't1' }], duplicates: [], message: '' });
    await useStockPoolStore.getState().submitBatchAnalysis(['600519']);
    useStockPoolStore.getState().resetDashboardState();
    const s = useStockPoolStore.getState();
    expect(s.isAnalyzingBatch).toBe(false);
    expect(s.batchSummary).toBeNull();
  });
});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `npx vitest run src/stores/__tests__/stockPoolStore.test.ts`
Expected: FAIL — `submitBatchAnalysis is not a function` / `isAnalyzingBatch` undefined。

- [ ] **Step 3: 加模块级 seq + 定时器变量**

在 `apps/dsa-web/src/stores/stockPoolStore.ts` 的模块级变量区（紧邻既有 `let analyzeRequestSeq = 0;` 一类 seq 变量，约 line 41-51）追加：

```typescript
/** 批量分析请求序号，防止旧请求的回包覆盖新请求的状态。 */
let batchRequestSeq = 0;
/** batchSummary 自动清空定时器句柄。 */
let batchSummaryTimerId: ReturnType<typeof setTimeout> | null = null;
```

- [ ] **Step 4: 扩 `StockPoolState` 接口 + `initialState`**

在 `StockPoolState` interface 内（约 line 53-123，紧邻 `isAnalyzing` / `duplicateError` 字段处）追加：

```typescript
  /** 批量分析进行中。 */
  isAnalyzingBatch: boolean;
  /** 最近一次批量分析的中文摘要（5s 后自动清空）。 */
  batchSummary: string | null;
```

并在该 interface 的方法区（紧邻 `submitAnalysis` 声明处）追加：

```typescript
  /** 把选中的股票代码批量加入待分析队列。 */
  submitBatchAnalysis: (stockCodes: string[]) => Promise<void>;
```

在 `initialState`（约 line 125-166）追加：

```typescript
    isAnalyzingBatch: false,
    batchSummary: null,
```

- [ ] **Step 5: 实现 `submitBatchAnalysis` action**

在 store 的 action 实现区、`submitAnalysis`（约 line 867）之后插入（沿用 `get`/`set`/`analysisApi`/`DuplicateTaskError`/`getParsedApiError` 既有引用；若 `getParsedApiError` 在本文件 import 名不同，按现有 `submitAnalysis` 的错误解析写法对齐）：

```typescript
  submitBatchAnalysis: async (stockCodes: string[]) => {
    // 防御性过滤：去空白、去重、排除 'MARKET'（大盘复盘伪项，非个股）。
    const codes = Array.from(
      new Set((stockCodes ?? []).map((c) => (typeof c === 'string' ? c.trim() : '')).filter((c) => c && c !== 'MARKET')),
    );
    if (codes.length === 0) {
      set({ inputError: '请选择有效的股票（不含大盘复盘）' });
      return;
    }
    const MAX_BATCH = 50;
    if (codes.length > MAX_BATCH) {
      set({ error: `一次最多批量分析 ${MAX_BATCH} 只，当前选中 ${codes.length} 只` });
      return;
    }
    const seq = ++batchRequestSeq;
    if (batchSummaryTimerId !== null) {
      clearTimeout(batchSummaryTimerId);
      batchSummaryTimerId = null;
    }
    set({
      isAnalyzingBatch: true,
      error: null,
      inputError: undefined,
      duplicateError: null,
      batchSummary: null,
    });
    try {
      const { notify } = get();
      const result = await analysisApi.analyzeBatch(codes, { notify });
      // 旧请求的回包：不覆盖新请求的状态。
      if (seq !== batchRequestSeq) return;
      const acceptedCount = result.accepted?.length ?? 0;
      const dupCount = result.duplicates?.length ?? 0;
      let summary: string;
      if (acceptedCount === 0 && dupCount > 0) {
        summary = `选中的 ${dupCount} 只均在分析中，已全部跳过`;
      } else if (dupCount > 0) {
        summary = `已加入 ${acceptedCount} 只，${dupCount} 只正在分析中已跳过`;
      } else {
        summary = `已加入 ${acceptedCount} 只到分析队列`;
      }
      set({ batchSummary: summary, isAnalyzingBatch: false });
      batchSummaryTimerId = setTimeout(() => {
        batchSummaryTimerId = null;
        // 仅当仍是同一序号时清空，避免清掉更新的摘要。
        if (seq === batchRequestSeq) set({ batchSummary: null });
      }, 5000);
    } catch (error) {
      if (seq !== batchRequestSeq) return;
      if (error instanceof DuplicateTaskError) {
        set({ duplicateError: error.stockCode, isAnalyzingBatch: false });
      } else {
        set({ error: getParsedApiError(error), isAnalyzingBatch: false });
      }
    }
  },
```

> 注：`getParsedApiError` 是 `submitAnalysis` 既有使用的错误解析器（见该 action 的 catch）。若实现时发现本文件对其引用名/来源不同，按 `submitAnalysis` 的实际写法对齐即可——逻辑不变。

- [ ] **Step 6: 在 `resetDashboardState` 中复位**

在 `resetDashboardState`（约 line 1033）内、重置既有 seq 计数器处追加：

```typescript
    batchRequestSeq = 0;
    if (batchSummaryTimerId !== null) {
      clearTimeout(batchSummaryTimerId);
      batchSummaryTimerId = null;
    }
```

`resetDashboardState` 已展开 `initialState`（含新加的 `isAnalyzingBatch`/`batchSummary`），二者随之复位。

- [ ] **Step 7: 运行测试，确认通过**

Run: `npx vitest run src/stores/__tests__/stockPoolStore.test.ts`
Expected: PASS（新增 9 个用例 + 既有用例不回归）。

- [ ] **Step 8: 类型检查 + 提交**

Run: `npx tsc --noEmit` — 无报错。

```bash
git add apps/dsa-web/src/stores/stockPoolStore.ts apps/dsa-web/src/stores/__tests__/stockPoolStore.test.ts
git commit -m "feat(web): add submitBatchAnalysis store action with dedup/filter/summary"
```

---

## Task 3: StockBar 批量分析按钮 + 摘要

**Files:**
- Modify: `apps/dsa-web/src/components/history/StockBar.tsx`
- Test: `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx`（新建）

- [ ] **Step 1: 写失败测试（新建测试文件）**

创建 `apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx`（默认中文文案渲染，无需 `UiLanguageProvider`，仿 `HistoryList.test.tsx`）：

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StockBar } from '../StockBar';
import type { StockBarItem } from '../../../types/analysis';

// 用 unknown 双重断言规避 StockBarItem 完整字段；测试只关心 stockCode/id。
const makeItem = (code: string, id: number) =>
  ({
    id,
    stockCode: code,
    stockName: code,
    sentimentScore: 0,
    sentimentLabel: '中性',
    action: 'hold',
    lastAnalysisTime: '2026-07-13 10:00:00',
    analysisCount: 1,
  }) as unknown as StockBarItem;

const baseItems = [makeItem('600519', 1), makeItem('000001', 2), makeItem('MARKET', 3)];

describe('StockBar 批量分析按钮', () => {
  it('未选中时批量分析按钮禁用', () => {
    render(
      <StockBar
        items={baseItems}
        isLoading={false}
        onItemClick={vi.fn()}
        onDeleteStock={vi.fn()}
        onAnalyzeSelected={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '批量分析' })).toBeDisabled();
  });

  it('全选后点击 → 仅提交非 MARKET 股并清空勾选', () => {
    const onAnalyzeSelected = vi.fn();
    render(
      <StockBar
        items={baseItems}
        isLoading={false}
        onItemClick={vi.fn()}
        onDeleteStock={vi.fn()}
        onAnalyzeSelected={onAnalyzeSelected}
      />,
    );
    // checkboxes[0] = 全选；点击后选中全部（含 MARKET）。
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);
    fireEvent.click(screen.getByRole('button', { name: '批量分析' }));
    expect(onAnalyzeSelected).toHaveBeenCalledWith(['600519', '000001']);
  });

  it('isAnalyzingBatch 时按钮禁用且显示加载文案', () => {
    render(
      <StockBar
        items={baseItems}
        isLoading={false}
        onItemClick={vi.fn()}
        onDeleteStock={vi.fn()}
        onAnalyzeSelected={vi.fn()}
        isAnalyzingBatch
      />,
    );
    // loadingText 传入「分析中」；Button 在 isLoading 时渲染 loadingText。
    const btn = screen.getByRole('button', { name: /分析中/ });
    expect(btn).toBeDisabled();
  });

  it('传入 batchSummary → 渲染摘要文本', () => {
    render(
      <StockBar
        items={baseItems}
        isLoading={false}
        onItemClick={vi.fn()}
        onDeleteStock={vi.fn()}
        onAnalyzeSelected={vi.fn()}
        batchSummary="已加入 2 只到分析队列"
      />,
    );
    expect(screen.getByText('已加入 2 只到分析队列')).toBeInTheDocument();
  });

  it('未传 batchSummary → 不渲染摘要', () => {
    render(
      <StockBar
        items={baseItems}
        isLoading={false}
        onItemClick={vi.fn()}
        onDeleteStock={vi.fn()}
        onAnalyzeSelected={vi.fn()}
      />,
    );
    expect(screen.queryByText(/已加入/)).not.toBeInTheDocument();
  });

  it('未传 onAnalyzeSelected → 不渲染批量分析按钮', () => {
    render(
      <StockBar items={baseItems} isLoading={false} onItemClick={vi.fn()} onDeleteStock={vi.fn()} />,
    );
    expect(screen.queryByRole('button', { name: '批量分析' })).not.toBeInTheDocument();
  });
});
```

> 校验点：`home.analyzing` 的中文文案须为「分析中」（见 Task 4 i18n）。若实际文案不同，按 `uiText.ts` 调整匹配正则。

- [ ] **Step 2: 运行测试，确认失败**

Run: `npx vitest run src/components/history/__tests__/StockBar.test.tsx`
Expected: FAIL — 找不到 name 为「批量分析」的按钮（按钮尚未渲染 / `onAnalyzeSelected` prop 不存在）。

- [ ] **Step 3: 扩 `StockBarProps` 接口 + 解构**

在 `apps/dsa-web/src/components/history/StockBar.tsx` 的 `StockBarProps`（line 9-18）追加 3 个可选 prop：

```typescript
interface StockBarProps {
  items: StockBarItemType[];
  isLoading: boolean;
  selectedStockCode?: string;
  selectedRecordId?: number;
  onItemClick: (recordId: number) => void;
  onDeleteStock?: (stockCode: string) => Promise<void> | void;
  isDeleting?: boolean;
  /** 批量分析回调，参数为已过滤 'MARKET' 的有效股票代码。 */
  onAnalyzeSelected?: (stockCodes: string[]) => void;
  /** 批量分析进行中（按钮加载/禁用）。 */
  isAnalyzingBatch?: boolean;
  /** 最近一次批量分析摘要（渲染于工具栏下）。 */
  batchSummary?: string | null;
  className?: string;
}
```

在组件参数解构（line 24-33）追加：

```typescript
export const StockBar: React.FC<StockBarProps> = ({
  items,
  isLoading,
  selectedStockCode,
  selectedRecordId,
  onItemClick,
  onDeleteStock,
  isDeleting = false,
  onAnalyzeSelected,
  isAnalyzingBatch = false,
  batchSummary = null,
  className = '',
}) => {
```

- [ ] **Step 4: 加 `handleAnalyzeSelected` handler**

在 `handleDeleteSelected`（line 67-74）之后插入：

```typescript
  const handleAnalyzeSelected = useCallback(() => {
    if (!onAnalyzeSelected || selectedCodes.size === 0 || isDeleting || isAnalyzingBatch) return;
    // 排除 'MARKET'（大盘复盘伪项，非个股）。
    const codes = [...selectedCodes].filter((c) => c && c !== 'MARKET');
    if (codes.length === 0) return;
    onAnalyzeSelected(codes);
    setSelectedCodes(new Set());
  }, [onAnalyzeSelected, selectedCodes, isDeleting, isAnalyzingBatch]);
```

- [ ] **Step 5: 工具栏加批量分析按钮 + 摘要渲染**

在工具栏 `flex items-center gap-2` 块（line 105-132）内、现有「删除」`<Button>`（line 122-131）之后，闭合 `</div>`（line 132）之前，追加批量分析按钮：

```tsx
              {onAnalyzeSelected && (
                <Button
                  variant="primary"
                  size="xsm"
                  onClick={() => void handleAnalyzeSelected()}
                  disabled={selectedCount === 0 || isDeleting || isAnalyzingBatch}
                  isLoading={isAnalyzingBatch}
                  loadingText={t('home.analyzing')}
                >
                  {t('common.batchAnalyze')}
                </Button>
              )}
```

在工具栏块闭合（line 133 的 `)}`）之后、`</div>`（line 134，`mb-4 space-y-3` 容器内）之前，追加摘要渲染：

```tsx
          {batchSummary && (
            <div className="px-2 text-[11px] text-info animate-in fade-in duration-200">
              {batchSummary}
            </div>
          )}
```

> 工具栏整体仍由 `items.length > 0 && onDeleteStock` 门控渲染（HomePage 两者皆传）；批量分析按钮自身再由 `onAnalyzeSelected` 门控，向后兼容。

- [ ] **Step 6: 运行测试，确认通过**

Run: `npx vitest run src/components/history/__tests__/StockBar.test.tsx`
Expected: PASS（6 个用例全绿）。若「分析中」匹配失败，核对 `uiText.ts` 的 `home.analyzing` 实际中文文案后修正测试正则（不改正文逻辑）。

- [ ] **Step 7: 类型检查 + 提交**

Run: `npx tsc --noEmit` — 无报错。

```bash
git add apps/dsa-web/src/components/history/StockBar.tsx apps/dsa-web/src/components/history/__tests__/StockBar.test.tsx
git commit -m "feat(web): add batch-analyze button + summary to StockBar"
```

---

## Task 4: i18n key + 透传 hook/HomePage 接线

**Files:**
- Modify: `apps/dsa-web/src/i18n/uiText.ts`
- Modify: `apps/dsa-web/src/hooks/useHomeDashboardState.ts`
- Modify: `apps/dsa-web/src/pages/HomePage.tsx`

- [ ] **Step 1: 新增 i18n key `common.batchAnalyze`**

在 `apps/dsa-web/src/i18n/uiText.ts` 中文段 `common.deleting`（约 line 9）之后插入：

```typescript
    batchAnalyze: '批量分析',
```

在英文段 `common.deleting`（约 line 809）之后插入：

```typescript
    batchAnalyze: 'Analyze',
```

> loading 文案复用既有 `home.analyzing`（中文约 line 152「分析中」/ 英文约 line 952「Analyzing」），不新增。

- [ ] **Step 2: `useHomeDashboardState` 转发 3 字段**

在 `apps/dsa-web/src/hooks/useHomeDashboardState.ts` 的 `useShallow` 选择器对象内、`submitAnalysis: state.submitAnalysis,`（line 59）之后追加 3 行：

```typescript
      isAnalyzingBatch: state.isAnalyzingBatch,
      batchSummary: state.batchSummary,
      submitBatchAnalysis: state.submitBatchAnalysis,
```

- [ ] **Step 3: HomePage 解构并透传**

在 `apps/dsa-web/src/pages/HomePage.tsx` 从 `useHomeDashboardState()` 解构处（约 line 93-138，紧邻 `submitAnalysis,`）追加：

```typescript
    submitBatchAnalysis,
    isAnalyzingBatch,
    batchSummary,
```

在 `sidebarContent` 的 `useMemo` 内渲染 `<StockBar>`（约 line 666-673）处，给 `<StockBar>` 追加 3 个 props：

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

并把 `submitBatchAnalysis`、`isAnalyzingBatch`、`batchSummary` 纳入该 `useMemo` 的依赖数组（否则会有 exhaustive-deps lint 警告）。

- [ ] **Step 4: 全量测试 + 类型检查 + 构建**

Run（在 `apps/dsa-web/` 下）:

```bash
npx vitest run
npx tsc --noEmit
npm run build
```

Expected: 全部测试通过（新增 4(api) + 9(store) + 6(StockBar) 用例 + 既有不回归）；tsc 无报错；build 成功。

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/i18n/uiText.ts apps/dsa-web/src/hooks/useHomeDashboardState.ts apps/dsa-web/src/pages/HomePage.tsx
git commit -m "feat(web): wire batch-analyze through HomePage + add common.batchAnalyze i18n"
```

---

## Self-Review（plan vs spec）

**Spec coverage:**
- §4.1 analyzeBatch → Task 1 ✓
- §4.2 store state + action + reset → Task 2 ✓
- §4.3 hook 转发 → Task 4 Step 2 ✓
- §4.4 StockBar props + 按钮 + 摘要 + handler → Task 3 ✓
- §4.5 HomePage 透传 → Task 4 Step 3 ✓
- §4.6 i18n `common.batchAnalyze` + 复用 `home.analyzing` → Task 4 Step 1 ✓
- §5 错误矩阵（空/超限/dup/全dup/409/5xx）→ Task 2 用例 + analyzeBatch 409 用例 ✓
- §6 测试（store 8 类 + StockBar 5 类）→ Task 1(4) + Task 2(9) + Task 3(6) ✓
- §8 验收：>50 横幅、MARKET 排除、duplicate 行内、删除互斥 → 由 Task 2/3 用例 + Step 4 全量验证覆盖 ✓

**Placeholder scan:** 无 TBD/TODO；每步含完整代码或确切命令。`getParsedApiError` 已注「按 submitAnalysis 既有写法对齐」。

**Type consistency:** `analyzeBatch(stockCodes, options?) → BatchTaskAcceptedResponse`、`submitBatchAnalysis(stockCodes:string[]) => Promise<void>`、StockBar `onAnalyzeSelected:(stockCodes:string[])=>void`、state `isAnalyzingBatch:boolean`/`batchSummary:string|null` —— 跨任务命名/签名一致。Button `variant:'primary'`/`size:'xsm'`/`isLoading`/`loadingText` 均为已验证的真实 prop。

**遗留风险（执行时确认）:**
1. `getParsedApiError` 的确切 import 名（按 `submitAnalysis` catch 对齐）。
2. `home.analyzing` 中文文案确为「分析中」（影响 StockBar 测试正则；非阻断）。
3. StockBar 测试不包 `UiLanguageProvider`——仿 `HistoryList.test.tsx`；若默认 context 报错则补 provider（非阻断，按既有测试惯例）。
4. HomePage `sidebarContent` useMemo 依赖数组的精确补项（lint 会提示）。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-13-stockbar-batch-analyze.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个 Task 派一个 fresh subagent，任务间我做两阶段评审，迭代快、上下文干净。
2. **Inline Execution** — 在本会话用 executing-plans 批量执行，带检查点评审。

Which approach?
