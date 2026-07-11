import apiClient from './index';
import { toCamelCase } from './utils';

/** 后台盘中盯盘运行状态（对应 GET /api/v1/monitor/status）。 */
export interface MonitorStatus {
  running: boolean;
  threadAlive: boolean;
  stockList: string[];
  intervalSeconds: number;
  rulesCount: number;
  /** 仅 /stop 响应包含：本次调用前是否存在并停止了实例。 */
  stoppedExisting?: boolean;
}

export const monitorApi = {
  async status(): Promise<MonitorStatus> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/monitor/status');
    return toCamelCase<MonitorStatus>(response.data);
  },

  async start(): Promise<MonitorStatus> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/monitor/start');
    return toCamelCase<MonitorStatus>(response.data);
  },

  async stop(): Promise<MonitorStatus> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/monitor/stop');
    return toCamelCase<MonitorStatus>(response.data);
  },
};
