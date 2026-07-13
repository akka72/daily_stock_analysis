import type React from 'react';
import { useState, useCallback, useRef, useEffect, useId } from 'react';
import { Badge, Button, ScrollArea } from '../common';
import { DashboardPanelHeader, DashboardStateBlock } from '../dashboard';
import { StockBarItemComponent } from './StockBarItem';
import type { StockBarItem as StockBarItemType } from '../../types/analysis';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

/** 大盘复盘伪项的股票代码（非真实个股，批量分析需排除）。 */
const MARKET_CODE = 'MARKET';

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

/**
 * 个股栏组件：以股票维度展示历史分析记录，每只股票只显示一条。
 * 大盘复盘可作为 MARKET 项参与展示，并按最近分析时间排序。
 */
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
  const { t } = useUiLanguage();
  const isMarketReview = (code: string) => code === MARKET_CODE;
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());
  const selectAllRef = useRef<HTMLInputElement>(null);
  const selectAllId = useId();

  const deletableItems = items;
  const selectedCount = [...selectedCodes].filter((code) => deletableItems.some((item) => item.stockCode === code)).length;
  /** 可批量分析的选中数：排除 'MARKET'（大盘复盘伪项）。与 handleAnalyzeSelected 的过滤口径一致。 */
  const analyzeableCount = [...selectedCodes].filter((c) => c && c !== MARKET_CODE).length;
  const allVisibleSelected = deletableItems.length > 0 && selectedCount === deletableItems.length;
  const someVisibleSelected = selectedCount > 0 && !allVisibleSelected;

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someVisibleSelected;
    }
  }, [someVisibleSelected]);

  const toggleCode = useCallback((code: string) => {
    setSelectedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedCodes((prev) => {
      if (prev.size === deletableItems.length) return new Set();
      return new Set(deletableItems.map((item) => item.stockCode));
    });
  }, [deletableItems]);

  const handleDeleteSelected = useCallback(async () => {
    if (!onDeleteStock || selectedCodes.size === 0) return;
    const codesToDelete = [...selectedCodes];
    for (const code of codesToDelete) {
      await onDeleteStock(code);
    }
    setSelectedCodes(new Set());
  }, [onDeleteStock, selectedCodes]);

  const handleAnalyzeSelected = useCallback(() => {
    if (!onAnalyzeSelected || selectedCodes.size === 0 || isDeleting || isAnalyzingBatch) return;
    // 排除 'MARKET'（大盘复盘伪项，非个股）。
    const codes = [...selectedCodes].filter((c) => c && c !== MARKET_CODE);
    // 无有效个股：不清空勾选（用户可能仍在调整选择），留待真正提交成功后再清空。
    if (codes.length === 0) return;
    onAnalyzeSelected(codes);
    setSelectedCodes(new Set());
  }, [onAnalyzeSelected, selectedCodes, isDeleting, isAnalyzingBatch]);

  return (
    <aside className={`glass-card overflow-hidden flex flex-col ${className}`}>
      <ScrollArea
        viewportClassName="p-4"
        testId="home-stock-bar-scroll"
      >
        <div className="mb-4 space-y-3">
          <DashboardPanelHeader
            className="mb-1"
            title={t('stockBar.title')}
            titleClassName="text-sm font-medium"
            leading={(
              <svg className="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
              </svg>
            )}
            headingClassName="items-center"
            actions={
              selectedCount > 0 ? (
                <Badge variant="info" size="sm" className="animate-in fade-in zoom-in duration-200">
                  {t('common.selectedCount', { count: selectedCount })}
                </Badge>
              ) : items.length > 0 ? (
                <span className="text-[11px] text-muted-text">{t('common.itemsCount', { count: items.length })}</span>
              ) : undefined
            }
          />

          {items.length > 0 && onDeleteStock && (
            <div className="flex items-center gap-2">
              <label
                className="flex flex-1 cursor-pointer items-center gap-2 rounded-lg px-2 py-1"
                htmlFor={selectAllId}
              >
                <input
                  id={selectAllId}
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allVisibleSelected}
                  onChange={toggleSelectAll}
                  disabled={isDeleting}
                  aria-label={t('history.selectAllStockAria')}
                  className="h-3.5 w-3.5 cursor-pointer bg-transparent accent-primary focus:ring-primary/30 disabled:opacity-50"
                />
                <span className="text-[11px] text-muted-text select-none">{t('common.selectAllCurrent')}</span>
              </label>
              <Button
                variant="danger-subtle"
                size="xsm"
                onClick={() => void handleDeleteSelected()}
                disabled={selectedCount === 0 || isDeleting}
                isLoading={isDeleting}
                className="disabled:!border-transparent disabled:!bg-transparent"
              >
                {isDeleting ? t('common.deleting') : t('common.delete')}
              </Button>
              {onAnalyzeSelected && (
                <Button
                  variant="primary"
                  size="xsm"
                  onClick={() => void handleAnalyzeSelected()}
                  disabled={analyzeableCount === 0 || isDeleting || isAnalyzingBatch}
                  isLoading={isAnalyzingBatch}
                  loadingText={t('home.analyzing')}
                >
                  {t('common.batchAnalyze')}
                </Button>
              )}
            </div>
          )}

          {batchSummary && (
            <div
              role="status"
              aria-live="polite"
              className="px-2 text-[11px] text-muted-text animate-in fade-in duration-200"
            >
              {batchSummary}
            </div>
          )}
        </div>

        {isLoading ? (
          <DashboardStateBlock
            loading
            compact
            title={t('stockBar.loading')}
          />
        ) : items.length === 0 ? (
          <DashboardStateBlock
            title={t('stockBar.emptyTitle')}
            description={t('stockBar.emptyDescription')}
            icon={(
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
          />
        ) : (
          <div className="space-y-1.5">
            {items.map((item) => {
              const code = item.stockCode || '';
              const isMarket = isMarketReview(code);
              const isSelected = selectedRecordId === item.id || selectedStockCode === code;
              const isChecked = selectedCodes.has(code);

              return (
                <div key={`${code}-${item.id}`} className="flex items-start gap-2 group">
                  {onDeleteStock && (
                    <div className="pt-5">
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleCode(code)}
                        disabled={isDeleting}
                        className="h-3.5 w-3.5 cursor-pointer rounded border-subtle-hover bg-transparent accent-primary focus:ring-primary/30 disabled:opacity-50"
                      />
                    </div>
                  )}
                  <StockBarItemComponent
                    item={item}
                    isViewing={isSelected}
                    onClick={onItemClick}
                    onDelete={onDeleteStock}
                    isDeleting={isDeleting}
                    isMarketReview={isMarket}
                  />
                </div>
              );
            })}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
};
