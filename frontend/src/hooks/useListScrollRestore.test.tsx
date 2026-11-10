/**
 * @file 列表滚动恢复与「返回列表」（2026-11-10）
 *
 * ## 两条行为各守一件事
 *
 * | Hook | 判据 |
 * |---|---|
 * | `useListScrollRestore` | **只在后退/前进时**恢复（`popstate`），PUSH 进来保持顶部 |
 * | `useBackToList` | 有上一页 → `navigate(-1)`（保留原 URL 与滚动）；深链接直达 → 打开列表 |
 *
 * jsdom 不实现 `window.scrollTo`（调用会抛"Not implemented"），所以下面
 * 一律把它换成 `vi.fn()`，并用 `Object.defineProperty` 造出"文档够高"的条件 ——
 * 恢复逻辑本身要求"高度够才滚"，否则它会一直重试到超时。
 */
import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { shouldGoBack, useBackToList } from './useBackToList';
import { useListScrollRestore } from './useListScrollRestore';

const STORAGE_KEY = 'engramnote:listScroll:notes';

/** 让文档"够高"，满足恢复前提 */
function stubTallDocument(height = 5000) {
  Object.defineProperty(document.documentElement, 'scrollHeight', {
    configurable: true,
    value: height,
  });
}

function stubScrolling() {
  const scrollTo = vi.fn();
  Object.defineProperty(window, 'scrollTo', {
    configurable: true,
    writable: true,
    value: scrollTo,
  });
  return scrollTo;
}

beforeEach(() => {
  sessionStorage.clear();
  stubTallDocument();
});

afterEach(() => {
  vi.useRealTimers();
});

function Probe({ restoreKey }: { restoreKey: string }) {
  useListScrollRestore(restoreKey);
  return null;
}

describe('useListScrollRestore', () => {
  it('★ 浏览器后退进来：滚回上次的位置', async () => {
    sessionStorage.setItem(STORAGE_KEY, '1200');
    const scrollTo = stubScrolling();
    // 后退/前进会先触发 popstate —— Hook 的模块级监听记下这个事实
    window.dispatchEvent(new PopStateEvent('popstate'));

    render(<Probe restoreKey="notes" />);

    await vi.waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 1200, behavior: 'auto' }));
  });

  it('点站内链接进来（没有 popstate）：不滚 —— 用户期待的是页面顶部', async () => {
    sessionStorage.setItem(STORAGE_KEY, '1200');
    const scrollTo = stubScrolling();

    render(<Probe restoreKey="notes" />);

    // 给恢复逻辑足够时间跑完（它内部有 1.5s 的上限）
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('离开时把当前滚动位置写进 sessionStorage', () => {
    stubScrolling();
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 640 });

    const { unmount } = render(<Probe restoreKey="notes" />);
    unmount();

    expect(sessionStorage.getItem(STORAGE_KEY)).toBe('640');
  });

  it('位置记在**不同的键**下：换筛选不会串到另一份列表', () => {
    stubScrolling();
    Object.defineProperty(window, 'scrollY', { configurable: true, value: 300 });

    const { unmount } = render(<Probe restoreKey="notes:material" />);
    unmount();

    expect(sessionStorage.getItem('engramnote:listScroll:notes:material')).toBe('300');
    expect(sessionStorage.getItem('engramnote:listScroll:notes')).toBeNull();
  });
});

describe('useBackToList', () => {
  it('★ 判据是历史深度：有上一页才后退（纯函数，直接覆盖边界）', () => {
    expect(shouldGoBack(0), '深度 0 = 直接进来，没有上一页').toBe(false);
    expect(shouldGoBack(undefined), 'state 缺失（老浏览器 / 首屏）也当作没有上一页').toBe(false);
    expect(shouldGoBack(1)).toBe(true);
    expect(shouldGoBack(7)).toBe(true);
  });

  it('深链接直达（没有上一页）→ 打开 fallback 列表', async () => {
    Object.defineProperty(window.history, 'state', { configurable: true, value: null });

    function ListPage() {
      return <p>笔记列表</p>;
    }
    function DetailPage() {
      const back = useBackToList('/notes');
      return (
        <button type="button" onClick={back}>
          返回列表
        </button>
      );
    }

    const { getByRole, findByText } = render(
      <MemoryRouter initialEntries={['/notes/abc']}>
        <Routes>
          <Route path="/notes" element={<ListPage />} />
          <Route path="/notes/abc" element={<DetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    getByRole('button', { name: '返回列表' }).click();
    expect(await findByText('笔记列表')).toBeInTheDocument();
  });
});
