/**
 * @file 认证上下文的登出行为测试（阶段 6.3）
 *
 * ## 为什么单独测登出
 *
 * 登出从"删本地字符串"变成了"先请求服务端撤销、再清本地"。这个改动引入了一个
 * 必须钉住的失败模式：**服务端撤销失败时用户还能不能登出？**
 *
 * 答案必须是"能"。否则网络抖动 / 后端 5xx 会让用户卡在一个自己已经不想用的
 * 会话里，而他唯一的补救手段（清掉本地令牌）恰好被那个异常挡住了。
 *
 * | 要证明的事 | 对应测试 |
 * |---|---|
 * | 登录保存**两个**令牌（只存访问令牌 = 丢掉撤销能力与续期能力） | `登录后同时保存访问令牌与刷新令牌` |
 * | 登出把本地刷新令牌交给服务端撤销 | `登出会把刷新令牌交给服务端撤销` |
 * | 服务端撤销失败时本地仍必须登出 | `服务端撤销失败也必须清干净本地状态` |
 * | 令牌过期事件把状态切成未登录 | `收到令牌过期事件后切到未登录` |
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// 只 mock 网络层：令牌存取、事件派发等真实实现必须走真实代码
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  };
});

import {
  TOKEN_EXPIRED_EVENT,
  clearTokens,
  getRefreshToken,
  getToken,
  login as apiLogin,
  logout as apiLogout,
  setTokens,
} from '../api/client';
import { AuthProvider, useAuth } from './AuthContext';

const mockLogin = vi.mocked(apiLogin);
const mockLogout = vi.mocked(apiLogout);

function Probe() {
  const { isAuthenticated, login, logout } = useAuth();
  return (
    <div>
      <span data-testid="state">{isAuthenticated ? 'in' : 'out'}</span>
      <button onClick={() => void login('a@b.c', 'pw')}>do-login</button>
      <button onClick={() => void logout()}>do-logout</button>
    </div>
  );
}

function renderProbe() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

describe('AuthContext 令牌生命周期', () => {
  beforeEach(() => {
    localStorage.clear();
    clearTokens();
    vi.clearAllMocks();
  });

  it('登录后同时保存访问令牌与刷新令牌', async () => {
    mockLogin.mockResolvedValue({
      access_token: 'a-1',
      refresh_token: 'r-1',
      token_type: 'bearer',
      user: {
        id: 'u-1',
        email: 'a@b.c',
        username: 'ab',
        is_active: true,
        created_at: '2026-01-01',
      },
    });
    renderProbe();
    await userEvent.click(screen.getByRole('button', { name: 'do-login' }));

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('in'));
    expect(getToken()).toBe('a-1');
    expect(getRefreshToken()).toBe('r-1');
  });

  it('登出会把刷新令牌交给服务端撤销', async () => {
    setTokens('a-1', 'r-1');
    mockLogout.mockResolvedValue({ revoked: 1 });
    renderProbe();

    await userEvent.click(screen.getByRole('button', { name: 'do-logout' }));

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('out'));
    expect(mockLogout).toHaveBeenCalledWith('r-1');
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('★ 服务端撤销失败也必须清干净本地状态（登出不能被网络故障挡住）', async () => {
    setTokens('a-1', 'r-1');
    mockLogout.mockRejectedValue(new Error('网络不通'));
    renderProbe();

    await userEvent.click(screen.getByRole('button', { name: 'do-logout' }));

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('out'));
    expect(getToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it('收到令牌过期事件后切到未登录', async () => {
    setTokens('a-1', 'r-1');
    renderProbe();
    expect(screen.getByTestId('state')).toHaveTextContent('in');

    // 刷新失败时 client.ts 派发的正是这个事件
    const { notifyTokenExpired } = await import('../api/client');
    notifyTokenExpired();

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('out'));
    expect(TOKEN_EXPIRED_EVENT).toBe('token-expired');
  });
});
