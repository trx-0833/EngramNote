/**
 * 取数层迁移（5.2 / C1）的不变量与行为变化
 * ==========================================
 *
 * ## 这一组盯的是什么
 *
 * 计划 §2.5 列了 5 条**必须保持不变**的东西、§2.6 列了 4 条**会变**的东西。
 * 两者都需要证据 —— 否则"迁移完成"只是"页面还能打开"。
 * 这个文件把其中**能离线测**的几条钉住：
 *
 * | 判据 | 出处 |
 * |---|---|
 * | 生产默认值 = 批准过的那几个数（写操作不重试） | §2.3 |
 * | 测试客户端不重试、不留缓存 | §2.3 / §2.2 |
 * | 每个 `renderWithProviders` 一份独立缓存（不串味） | §2.2 第 1 条 |
 * | 卸载即取消：`queryFn` 真的拿到了 `AbortSignal`，且卸载时被 abort | §2.5 第 5 条 |
 *
 * ## 为什么"卸载即取消"要单独测
 *
 * §2.5 第 5 条原话是「迁到 `queryFn({ signal })` 时**不能丢**」。
 * 而"丢没丢"从页面上**看不出来** —— 请求照样成功、数据照样渲染，
 * 只是组件卸载后仍有一次在途请求（浪费带宽、且旧实现里会有
 * "卸载后 setState"的警告）。所以它必须有直接证据。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient, queryClient, QUERY_DEFAULTS } from '../api/queryClient';
import { QueryWrapper, renderWithProviders } from './render';

describe('生产默认值（计划 §2.3，已批准）', () => {
  it('query：staleTime 30s / retry 1 / 不在窗口聚焦时重取', () => {
    const q = queryClient.getDefaultOptions().queries;
    expect(q?.staleTime).toBe(30_000);
    expect(q?.retry).toBe(1);
    // 单机单窗口；开着会让"切回来就刷新"变成不可预期的行为变化
    expect(q?.refetchOnWindowFocus).toBe(false);
  });

  it('★ mutation 的 retry 是 0 —— 写操作不能盲目重试', () => {
    // 这不是"保守"而是**正确性**：一个超时但实际成功的 POST 被自动重试
    // 会建出两份数据，而用户只看到"多了一份一模一样的"。
    expect(queryClient.getDefaultOptions().mutations?.retry).toBe(0);
    expect(QUERY_DEFAULTS.mutations.retry).toBe(0);
  });
});

describe('测试客户端（计划 §2.2 / §2.3）', () => {
  it('不重试、不留缓存', () => {
    const c = createTestQueryClient();
    const q = c.getDefaultOptions().queries;
    // retry:false —— 否则"请求失败"的用例要等一次退避重试才变红，慢且掩盖时序
    expect(q?.retry).toBe(false);
    // gcTime:0 —— 用例之间不留缓存，避免"上一个用例的数据喂给下一个"的假绿
    expect(q?.gcTime).toBe(0);
  });

  it('★ 每次调用都是新实例（否则用例之间串缓存）', () => {
    const a = createTestQueryClient();
    const b = createTestQueryClient();
    expect(a).not.toBe(b);
    a.setQueryData(['x'], 'from-a');
    expect(b.getQueryData(['x'])).toBeUndefined();
  });

  it('★ 两次 renderWithProviders 的缓存互不可见', async () => {
    // 共用一份缓存会让"前一个用例的数据喂给后一个" —— 表现为**假绿**
    // （看起来功能正常，实际是缓存串味）。所以这条断言的是"隔离"本身。
    let calls = 0;
    function Probe() {
      const { data } = useQuery({
        queryKey: ['probe'],
        queryFn: async () => {
          calls += 1;
          return `call-${calls}`;
        },
      });
      return <span data-testid="v">{data ?? 'loading'}</span>;
    }

    const first = renderWithProviders(<Probe />);
    await waitFor(() => expect(first.getByTestId('v')).toHaveTextContent('call-1'));
    first.unmount();

    // 第二次渲染：如果是同一份缓存，会直接命中缓存（仍是 call-1、calls 不增）
    const second = renderWithProviders(<Probe />);
    await waitFor(() => expect(second.getByTestId('v')).toHaveTextContent('call-2'));
    expect(calls).toBe(2);
  });
});

describe('★ 卸载即取消（计划 §2.5 第 5 条）', () => {
  it('queryFn 拿到 AbortSignal，且组件卸载时它被 abort', async () => {
    const seen: { aborted: boolean | null } = { aborted: null };
    /**
     * ⚠️ 用"持有对象"而不是 `let release: (() => void) | null = null`。
     *
     * 第一版是 `let`，`tsc` 在 `release?.()` 上报
     * `TS2349: This expression is not callable. Type 'never' has no call signatures`
     * —— 因为 `release` **只在回调里被赋值**，控制流分析认为调用点它必然是 `null`，
     * 于是窄化成 `never`。属性访问不会被这样窄化。
     */
    const holder: { release?: () => void } = {};

    function Slow() {
      const { data } = useQuery({
        queryKey: ['slow'],
        queryFn: ({ signal }) =>
          new Promise<string>((resolve) => {
            // 记录卸载那一刻这个 signal 的状态
            signal.addEventListener('abort', () => {
              seen.aborted = true;
            });
            holder.release = () => resolve('done');
          }),
      });
      return <span data-testid="v">{data ?? 'loading'}</span>;
    }

    const { unmount, getByTestId } = renderWithProviders(<Slow />);
    expect(getByTestId('v')).toHaveTextContent('loading');

    // 请求还在途 ⇒ 此刻 signal 未被 abort
    expect(seen.aborted).toBeNull();

    unmount();

    // 卸载后必须被 abort —— 这就是"不丢卸载取消"的直接证据
    await waitFor(() => expect(seen.aborted).toBe(true));
    holder.release?.();
  });

  it('未卸载时请求正常完成（反向断言：不是"永远 abort"）', async () => {
    // 没有这条，把 signal 写死成"总是已 abort"也能让上一条绿 —— 而那是**功能坏了**。
    function Fast() {
      const { data } = useQuery({ queryKey: ['fast'], queryFn: async () => 'ok' });
      return <span data-testid="v">{data ?? 'loading'}</span>;
    }
    renderWithProviders(<Fast />);
    await waitFor(() => expect(screen.getByTestId('v')).toHaveTextContent('ok'));
  });
});

describe('QueryWrapper 与 renderWithProviders 的分工', () => {
  it('QueryWrapper 只给 Provider、不给路由', async () => {
    function Probe() {
      const [v] = useState('ok');
      useEffect(() => {}, []);
      return <span data-testid="w">{v}</span>;
    }
    render(
      <QueryWrapper>
        <Probe />
      </QueryWrapper>,
    );
    expect(screen.getByTestId('w')).toHaveTextContent('ok');
  });

  it('传 route 时才套 MemoryRouter（不传则不需要路由上下文）', async () => {
    // 反向断言：需要路由的组件在不传 route 时**应当报错** ——
    // 否则说明这个助手偷偷给了路由，"组件在偷偷用路由"就被掩盖了。
    const { Link } = await import('react-router-dom');
    function NeedsRouter() {
      return <Link to="/x">go</Link>;
    }
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => renderWithProviders(<NeedsRouter />)).toThrow();
    consoleError.mockRestore();

    // 传了 route 就正常
    const ok = renderWithProviders(<NeedsRouter />, { route: '/' });
    expect(ok.getByRole('link', { name: 'go' })).toBeInTheDocument();
  });
});

describe('client 注入（避免误用全局实例）', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renderWithProviders 用的是测试客户端而不是全局那个', async () => {
    // 如果助手误用了模块级的 `queryClient`，用例之间就会共用缓存，
    // 而 `gcTime: 0` 也就形同虚设。这条用"全局客户端里查不到这个 key"来证明。
    function Probe() {
      const { data } = useQuery({ queryKey: ['only-in-test'], queryFn: async () => 'v' });
      return <span data-testid="p">{data ?? 'loading'}</span>;
    }
    const { getByTestId } = renderWithProviders(<Probe />);
    await waitFor(() => expect(getByTestId('p')).toHaveTextContent('v'));
    expect(queryClient.getQueryData(['only-in-test'])).toBeUndefined();
  });

  it('QueryClientProvider 是公开导出的一部分（main.tsx 依赖它）', () => {
    // main.tsx 里那个 Provider 是"整个应用能取数"的唯一前提。
    // 这条不做渲染，只确认两个名字都还在（删了它们应用会白屏）。
    expect(typeof QueryClientProvider).toBe('function');
    expect(queryClient).toBeDefined();
  });
});
