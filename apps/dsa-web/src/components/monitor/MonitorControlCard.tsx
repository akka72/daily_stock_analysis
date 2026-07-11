import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Activity, Play, Square } from 'lucide-react';
import { monitorApi, type MonitorStatus } from '../../api/monitor';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { ApiErrorAlert, Button, Card, InlineAlert, StatusDot } from '../common';

/** 自动刷新盯盘状态的间隔（毫秒）。运行中时让状态灯/线程活性保持新鲜。 */
const REFRESH_INTERVAL_MS = 10000;

interface MonitorControlCardProps {
  className?: string;
}

/**
 * 盘中盯盘运行时控制卡：展示后台 RealtimeMonitor 线程状态，
 * 并允许在 WebUI 内一键启动/停止，无需重启 serve。
 */
export const MonitorControlCard: React.FC<MonitorControlCardProps> = ({ className }) => {
  const [status, setStatus] = useState<MonitorStatus | null>(null);  const [acting, setActing] = useState<'start' | 'stop' | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await monitorApi.status();
      setStatus(next);
      setError(null);
    } catch (err) {
      setError(getParsedApiError(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  const handleStart = async () => {
    setActing('start');
    setError(null);
    setNotice(null);
    try {
      const next = await monitorApi.start();
      setStatus(next);
      setNotice('盯盘已启动（按最新配置重建实例）');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(null);
    }
  };

  const handleStop = async () => {
    setActing('stop');
    setError(null);
    setNotice(null);
    try {
      const next = await monitorApi.stop();
      setStatus(next);
      setNotice('已请求停止盯盘（当前轮询结束后退出）');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setActing(null);
    }
  };

  const running = !!status?.running;
  const intervalSeconds = status?.intervalSeconds ?? 0;
  const stockList = status?.stockList ?? [];
  const rulesCount = status?.rulesCount ?? 0;
  const stockPreview = stockList.length > 0
    ? `${stockList.length} 只 · ${stockList.slice(0, 6).join(', ')}${stockList.length > 6 ? ' …' : ''}`
    : '--';

  return (
    <Card title="盘中盯盘" subtitle="Realtime Monitor" variant="bordered" padding="md" className={className}>
      {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}
      {notice ? (
        <InlineAlert
          title="操作结果"
          message={notice}
          variant="success"
          action={(
            <button type="button" className="text-sm underline" onClick={() => setNotice(null)}>
              关闭
            </button>
          )}
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <StatusDot
            tone={running ? 'success' : 'neutral'}
            pulse={running}
            aria-label={running ? '运行中' : '已停止'}
          />
          <span className="text-sm font-medium text-foreground">
            {running ? '盯盘运行中' : '盯盘未运行'}
          </span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            onClick={() => void handleStart()}
            disabled={running || acting !== null}
            isLoading={acting === 'start'}
            loadingText="启动中"
          >
            <Play className="h-4 w-4" />
            启动盯盘
          </Button>
          <Button
            variant="danger-subtle"
            size="sm"
            onClick={() => void handleStop()}
            disabled={!running || acting !== null}
            isLoading={acting === 'stop'}
            loadingText="停止中"
          >
            <Square className="h-4 w-4" />
            停止盯盘
          </Button>
        </div>
      </div>

      <div className="mt-3 grid gap-2 text-xs text-muted-text sm:grid-cols-3">
        <div>
          <span className="label-uppercase">轮询间隔</span>
          <div className="mt-0.5 text-sm text-foreground">
            {intervalSeconds > 0 ? `${intervalSeconds} 秒` : '--'}
          </div>
        </div>
        <div>
          <span className="label-uppercase">股票池</span>
          <div className="mt-0.5 text-sm text-foreground">{stockPreview}</div>
        </div>
        <div>
          <span className="label-uppercase">激活规则</span>
          <div className="mt-0.5 text-sm text-foreground">{rulesCount > 0 ? `${rulesCount} 条` : '--'}</div>
        </div>
      </div>

      <div className="mt-3 flex items-start gap-2 text-xs text-muted-text">
        <Activity className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          启动时按当前配置（轮询间隔、告警规则 JSON、连续绿柱维度等）重建盯盘实例；在「设置」改完点「启动盯盘」即生效，无需重启服务。
        </span>
      </div>
    </Card>
  );
};
