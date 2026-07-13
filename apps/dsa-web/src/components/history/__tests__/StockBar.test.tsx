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
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);
    fireEvent.click(screen.getByRole('button', { name: '批量分析' }));
    expect(onAnalyzeSelected).toHaveBeenCalledWith(['600519', '000001']);
    // 提交成功后清空勾选 → 全选复选框应恢复未勾选状态。
    expect(screen.getAllByRole('checkbox')[0]).not.toBeChecked();
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
