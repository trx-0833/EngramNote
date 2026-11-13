/**
 * @file 问答历史接口层的判据（docs/qa-history-plan.md §8.8）
 *
 * 四件事各自防一种"看起来对但用不了"的写法：
 *
 * | 判据 | 坏掉的后果 |
 * |---|---|
 * | 查询参数只带非空项 | 空 `keyword` 变成 `keyword=`，后端把"搜到空字符串"当过滤条件，列表永远空 |
 * | `DELETE` 走 204 分支 | 用 `request()` 解析空的 204 响应体会抛 JSON 解析错误 —— 用户看到"删除失败"，其实已经删了 |
 * | 「清空全部」返回条数 | 界面拿不到"清掉几个"，只能静默 |
 * | 错误码透传 | 会话已删时前端无法区分"网络错"与"这个 id 没了"，只能笼统报错 |
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { ApiError } from './client';
import {
  clearQaSessions,
  deleteQaSession,
  getQaSessions,
  getQaSession,
  isSessionGoneError,
} from './qaHistory';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 造一个能通过 `authorizedFetch` 的最小 token */
function seedToken() {
  window.localStorage.setItem('access_token', 'test-token');
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  seedToken();
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('getQaSessions', () => {
  it('只把非空参数拼进查询串（空 keyword 不产生参数）', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }));

    await getQaSessions({ page: 2, page_size: 50, keyword: '   ', surface: '' });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain('page=2');
    expect(url).toContain('page_size=50');
    expect(url).not.toContain('keyword=');
    expect(url).not.toContain('surface=');
  });

  it('中文关键词被编码且能原样解回（空格允许编码成 +）', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [], total: 0 }));

    await getQaSessions({ keyword: '浮充 寿命' });

    const url = String(fetchMock.mock.calls[0][0]);
    // `URLSearchParams` 把空格编码成 `+`（表单编码），FastAPI 侧同样按 `+` 解回空格，
    // 所以这里断言"能解回原值"而不是断言某种具体编码 —— 后者会把实现细节钉死。
    expect(new URLSearchParams(url.split('?')[1]).get('keyword')).toBe('浮充 寿命');
    expect(url).not.toContain(' ');
  });
});

describe('getQaSession', () => {
  it('会话已删时抛出带 error_code 的 ApiError', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: '对话不存在或已被删除', error_code: 'QA_SESSION_NOT_FOUND' }, 404),
    );

    await expect(getQaSession('gone')).rejects.toMatchObject({
      code: 'QA_SESSION_NOT_FOUND',
    });
  });

  it('会话 id 会被编码（防路径注入）', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ session: {}, turns: [] }));

    await getQaSession('a/b c');

    expect(String(fetchMock.mock.calls[0][0])).toContain('a%2Fb%20c');
  });
});

describe('deleteQaSession', () => {
  it('204 无响应体时正常返回（不尝试解析 JSON）', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(deleteQaSession('s1')).resolves.toBeUndefined();
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('DELETE');
    // 守的是「删除只发一次请求」：`authorizedFetch` 一旦加上"失败重试"，
    // 这里立刻红。破坏性请求被发两次的后果是"第一次其实已成功、第二次拿到 404"，
    // 用户会看到"对话不存在"而列表仍是旧的（审计 m4）。
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('再次删除（404）抛出 QA_SESSION_NOT_FOUND', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: '对话不存在或已被删除', error_code: 'QA_SESSION_NOT_FOUND' }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    await expect(deleteQaSession('s1')).rejects.toBeInstanceOf(ApiError);
    // 同上：**失败分支也不许补发一次**。原先的错误本地化正是又调了一次
    // `deleteQaSession`，于是失败路径上 DELETE 被发了两次。
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('clearQaSessions', () => {
  it('返回删掉的会话条数', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ deleted: 7 }));

    await expect(clearQaSessions()).resolves.toEqual({ deleted: 7 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain('/qa/sessions');
    expect(init.method).toBe('DELETE');
  });
});

describe('isSessionGoneError', () => {
  it('只认 QA_SESSION_NOT_FOUND', () => {
    expect(isSessionGoneError(new ApiError('x', 'QA_SESSION_NOT_FOUND'))).toBe(true);
    expect(isSessionGoneError(new ApiError('x', 'OTHER'))).toBe(false);
    expect(isSessionGoneError(new Error('network'))).toBe(false);
    expect(isSessionGoneError(undefined)).toBe(false);
  });
});
