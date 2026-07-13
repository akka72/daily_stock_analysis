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

  it('单只成功 → 后端 flat TaskAccepted 不含 stock_code，归一化时回填输入代码', async () => {
    // 后端 _handle_async_analysis_batch 对单只成功返回 flat TaskAccepted（无 stock_code）。
    post.mockResolvedValueOnce({
      status: 202,
      data: { task_id: 't1', status: 'pending', message: 'ok' },
    });
    const result = await analysisApi.analyzeBatch(['600519']);
    expect(result.accepted).toHaveLength(1);
    expect(result.accepted[0].taskId).toBe('t1');
    expect(result.accepted[0].stockCode).toBe('600519');
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
