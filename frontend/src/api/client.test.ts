/**
 * @file API 客户端的刷新与重试链路测试（阶段 6.3）
 *
 * ## 这份测试要证明什么
 *
 * "401 → 刷新 → 重放一次"这段逻辑有两个**必然出错的写法**，而它们都不会
 * 在手工点几下页面时暴露：
 *
 * 1. **重放不止一次**（或刷新失败后继续重放）→ 请求风暴，服务端被刷爆；
 * 2. **并发 401 各刷一次** → 服务端开启了轮换：第一次刷新会把提交的令牌
 *    标记为已撤销，第二次提交的就是那枚刚被撤销的令牌，服务端判定为
 *    **重放（令牌被盗）** 并撤销整条链 —— 用户被莫名其妙地登出。
 *    这不是理论风险：一个仪表盘页面同时打 5 个接口，令牌一过期就会命中。
 *
 * 因此这里盯的是"次数"与"共享"：
 *
 * | 要证明的事 | 对应测试 |
 * |---|---|
 * | 401 → 刷新一次 → 重放一次 → 成功，且重放带的是**新**令牌 | `refreshes once and replays with the new token` |
 * | 重放后仍 401 → 不再重试，清凭据 + 通知过期 | `replays only once` |
 * | 刷新失败 → 不重放原请求，清凭据 + 通知过期 | `clears the session when refresh fails` |
 * | 并发 401 **只刷新一次**（轮换下的硬约束） | `shares a single refresh across concurrent 401s` |
 * | 本地没有刷新令牌时不去打刷新接口 | `does not call refresh without a stored refresh token` |
 * | 登录/注册的 401 不触发刷新（那是密码错） | `credential 401 does not trigger a refresh` |
 * | 刷新接口自身的 401 不递归刷新 | `refresh endpoint never triggers a nested refresh` |
 * | 上传/流式接口同样享受刷新重放 | `uploadRequest` / `askQuestionStream` 两条 |
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  authorizedFetch,
  askQuestionStream,
  clearTokens,
  getRefreshToken,
  getToken,
  notifyTokenExpired,
  refreshSession,
  request,
  setTokens,
  TOKEN_EXPIRED_EVENT,
  uploadRequest,
} from './client';

/** 手搓最小响应对象：不依赖 jsdom 里是否有 Response 构造器 */
function jsonResponse(status: number, body?: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  } as unknown as Response;
}

/** 一次成功刷新的响应体 */
function freshPair(access = 'new-access', refresh = 'new-refresh') {
  return { access_token: access, refresh_token: refresh };
}

/** 取某次 fetch 调用的 URL 与 init */
function callAt(mock: ReturnType<typeof vi.fn>, index: number): [string, RequestInit] {
  const call = mock.mock.calls[index];
  return [String(call[0]), (call[1] ?? {}) as RequestInit];
}

/** 取某次 fetch 调用的 Authorization 头 */
function authHeaderAt(mock: ReturnType<typeof vi.fn>, index: number): string | undefined {
  const [, init] = callAt(mock, index);
  return (init.headers as Record<string, string> | undefined)?.Authorization;
}

describe('API 客户端：令牌存取', () => {
  beforeEach(() => localStorage.clear());

  it('保存并读取一对令牌', () => {
    setTokens('a-1', 'r-1');
    expect(getToken()).toBe('a-1');
    expect(getRefreshToken()).toBe('r-1');
  });

  it('setTokens 不带刷新令牌时清掉旧值（避免旧会话残留）', () => {
    setTokens('a-1', 'r-1');
    setTokens('a-2');
    expect(getToken()).toBe('a-2');
    expect(getRefreshToken()).toBeNull();
  });

  it('clearTokens 两个都清', () => {
    setTokens('a-1', 'r-1');
    clearTokens();
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('notifyTokenExpired 清凭据并派发全局事件', () => {
    setTokens('a-1', 'r-1');
    const seen = vi.fn();
    window.addEventListener(TOKEN_EXPIRED_EVENT, seen);
    notifyTokenExpired();
    window.removeEventListener(TOKEN_EXPIRED_EVENT, seen);

    expect(seen).toHaveBeenCalledTimes(1);
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });
});

describe('API 客户端：401 → 刷新 → 重放', () => {
  let mockFetch: ReturnType<typeof vi.fn>;
  let expiredEvents: number;
  let onExpired: () => void;

  beforeEach(() => {
    localStorage.clear();
    setTokens('old-access', 'old-refresh');
    expiredEvents = 0;
    onExpired = () => {
      expiredEvents += 1;
    };
    window.addEventListener(TOKEN_EXPIRED_EVENT, onExpired);
    mockFetch = vi.fn();
    vi.stubGlobal('fetch', mockFetch);
  });

  afterEach(() => {
    // 必须摘掉监听器：不然事件计数会跨用例累加，断言"通知了几次"就失去意义
    window.removeEventListener(TOKEN_EXPIRED_EVENT, onExpired);
    vi.unstubAllGlobals();
  });

  it('★ 401 → 刷新一次 → 重放一次，重放带的是新令牌', async () => {
    mockFetch
      .mockResolvedValueOnce(jsonResponse(401, { detail: '令牌过期' }))
      .mockResolvedValueOnce(jsonResponse(200, freshPair()))
      .mockResolvedValueOnce(jsonResponse(200, { items: [] }));

    const result = await request<{ items: unknown[] }>('/notes');

    expect(result).toEqual({ items: [] });
    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(callAt(mockFetch, 1)[0]).toBe('/api/auth/refresh');
    // 重放用的是**新**访问令牌，而不是重放旧的那一枚
    expect(authHeaderAt(mockFetch, 0)).toBe('Bearer old-access');
    expect(authHeaderAt(mockFetch, 2)).toBe('Bearer new-access');
    expect(getToken()).toBe('new-access');
    expect(getRefreshToken()).toBe('new-refresh');
    expect(expiredEvents).toBe(0);
  });

  it('★ 重放只发生一次：重放后仍 401 就放弃，不再刷新', async () => {
    mockFetch
      .mockResolvedValueOnce(jsonResponse(401, { detail: '过期' }))
      .mockResolvedValueOnce(jsonResponse(200, freshPair()))
      .mockResolvedValueOnce(jsonResponse(401, { detail: '还是过期' }));

    await expect(request('/notes')).rejects.toThrow('登录已过期，请重新登录');

    // 3 次 = 原请求 + 刷新 + 重放；没有第 4 次（没有第二轮刷新/重放）
    expect(mockFetch).toHaveBeenCalledTimes(3);
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(expiredEvents).toBe(1);
  });

  it('★ 刷新失败：不重放原请求，清凭据并通知回登录页', async () => {
    mockFetch
      .mockResolvedValueOnce(jsonResponse(401, { detail: '过期' }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: '刷新令牌无效' }));

    await expect(request('/notes')).rejects.toThrow('登录已过期，请重新登录');

    expect(mockFetch).toHaveBeenCalledTimes(2); // 原请求 + 刷新，没有重放
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(expiredEvents).toBe(1);
  });

  it('★ 并发 401 只刷新一次（轮换下重复刷新会触发服务端重放检测）', async () => {
    let refreshCalls = 0;
    // 假服务端：只认刷新后的新令牌，其余一律 401
    mockFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url) === '/api/auth/refresh') {
        refreshCalls += 1;
        return jsonResponse(200, freshPair());
      }
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      return auth === 'Bearer new-access'
        ? jsonResponse(200, { ok: true })
        : jsonResponse(401, { detail: '过期' });
    });

    const [a, b, c] = await Promise.all([
      request<{ ok: boolean }>('/notes'),
      request<{ ok: boolean }>('/cards'),
      request<{ ok: boolean }>('/review/due'),
    ]);

    expect([a.ok, b.ok, c.ok]).toEqual([true, true, true]);
    // 三个请求共用了同一次刷新（否则服务端会把第二次提交当成重放并撤销整链）
    expect(refreshCalls).toBe(1);
  });

  it('本地没有刷新令牌时不去打刷新接口（旧会话/未登录）', async () => {
    setTokens('old-access'); // 只存访问令牌，模拟改造前留下的会话
    mockFetch.mockResolvedValueOnce(jsonResponse(401, { detail: '过期' }));

    await expect(request('/notes')).rejects.toThrow('登录已过期，请重新登录');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(getToken()).toBeNull();
    expect(expiredEvents).toBe(1);
  });

  it('登录/注册的 401 不触发刷新（那是密码错误，不是令牌过期）', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(401, { detail: '邮箱或密码错误' }));

    await expect(request('/auth/login', { method: 'POST' })).rejects.toThrow('邮箱或密码错误');

    expect(mockFetch).toHaveBeenCalledTimes(1);
    // 凭据接口的 401 不应把已有的会话状态抹掉
    expect(getToken()).toBe('old-access');
    expect(expiredEvents).toBe(0);
  });

  it('刷新接口自身的 401 不触发嵌套刷新（防递归）', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(401, { detail: '刷新令牌无效' }));

    await expect(request('/auth/refresh', { method: 'POST', body: '{}' })).rejects.toThrow(
      '登录已过期，请重新登录',
    );

    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('非 401 错误不触发刷新（404/500 与令牌无关）', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(500, { detail: '服务器错误' }));

    await expect(request('/notes')).rejects.toThrow('服务器错误');
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(getToken()).toBe('old-access');
  });

  it('上传请求同样会刷新并重放（FormData 原样再发一次）', async () => {
    mockFetch
      .mockResolvedValueOnce(jsonResponse(401, { detail: '过期' }))
      .mockResolvedValueOnce(jsonResponse(200, freshPair()))
      .mockResolvedValueOnce(jsonResponse(200, { id: 'note-1' }));

    const form = new FormData();
    form.append('file', new Blob(['x']), 'a.pdf');
    const result = await uploadRequest<{ id: string }>('/upload', form);

    expect(result).toEqual({ id: 'note-1' });
    expect(mockFetch).toHaveBeenCalledTimes(3);
    const [, retryInit] = callAt(mockFetch, 2);
    // 重放必须带上原来的请求体（少了它上传会变成"空文件"）
    expect(retryInit.body).toBe(form);
    expect(authHeaderAt(mockFetch, 2)).toBe('Bearer new-access');
  });

  it('流式问答同样会刷新并重放', async () => {
    const stream = {} as ReadableStream<Uint8Array>;
    mockFetch
      .mockResolvedValueOnce(jsonResponse(401, { detail: '过期' }))
      .mockResolvedValueOnce(jsonResponse(200, freshPair()))
      .mockResolvedValueOnce({ status: 200, ok: true, body: stream } as unknown as Response);

    await expect(askQuestionStream('问题')).resolves.toBe(stream);
    expect(mockFetch).toHaveBeenCalledTimes(3);
  });

  it('authorizedFetch 在没有令牌时不发 Authorization 头', async () => {
    localStorage.clear();
    mockFetch.mockResolvedValueOnce(jsonResponse(200, {}));

    await authorizedFetch('/notes', {});

    expect(authHeaderAt(mockFetch, 0)).toBeUndefined();
  });

  it('refreshSession 在没有刷新令牌时立即返回 false 且不发请求', async () => {
    localStorage.clear();
    await expect(refreshSession()).resolves.toBe(false);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('refreshSession 成功后落地新令牌对，失败则保持原样（不半更新）', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse(200, freshPair('a-2', 'r-2')));
    await expect(refreshSession()).resolves.toBe(true);
    expect(getToken()).toBe('a-2');
    expect(getRefreshToken()).toBe('r-2');

    // 响应缺字段（半个令牌对）必须按失败处理：否则下一次刷新会用已撤销的令牌
    setTokens('a-3', 'r-3');
    mockFetch.mockResolvedValueOnce(jsonResponse(200, { access_token: 'a-4' }));
    await expect(refreshSession()).resolves.toBe(false);
    expect(getToken()).toBe('a-3');
    expect(getRefreshToken()).toBe('r-3');
  });
});
