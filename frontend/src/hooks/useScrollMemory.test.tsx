/**
 * @file 阅读位置记忆 Hook 的行为测试（`hooks/useScrollMemory.ts`）
 *
 * ## 这一层要钉住的是「刷新后回到原处」这个用户场景
 *
 * 「使用时的想法.txt」第 1 条的原话是：
 * > 切至别的窗口干活之后，再回来一刷新就不知道看到哪里了……
 * > 建议设计一个小功能，能够短暂地记忆上一次看到哪里了。
 *
 * 刷新 = 组件**重新挂载**，所以核心用例就是「卸载 → 重新挂载 → 滚回原位」。
 * 但真正难验的是它的**时机**：正文是异步渲染的，在它撑开文档高度之前
 * 滚过去会被浏览器钳住（"滚了一半"比"回到顶部"更让人困惑）。
 * 所以下面把三条边界都钉死：能滚到才滚、滚不到就等、等不到就放弃。
 *
 * ## 关于桩（都是**测量工具**，不是产品逻辑）
 *
 * jsdom 不做布局、不实现滚动：
 * - `window.scrollY` 恒为 0 且是只读 getter ⇒ `defineProperty` 装一个可写的值
 * - `window.scrollTo` 会往虚拟控制台写 "Not implemented" ⇒ 换成 spy
 * - `document.documentElement.scrollHeight` 恒为 0 ⇒ 装一个可控的值
 * - `requestAnimationFrame` **jsdom 默认没有**（`vite.config.ts` 没开
 *   `pretendToBeVisual`）⇒ 用 `setTimeout` 替代，重试照常发生
 * - `ResizeObserver` 同样没有 ⇒ 提供一个可控的假实现，用来驱动"高度变了"
 *
 * 这几行与 `DailyMaterials.test.tsx:118-130` 的 `stubScrolling` 是同款做法。
 */
import { act, render, screen } from '@testing-library/react';
import { useRef, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readScrollMemory, writeScrollMemory } from '../utils/scrollMemory';
import { useScrollMemory } from './useScrollMemory';

const NOW = 1_800_000_000_000;

/** 当前"窗口高度"与"文档高度"，用例按需改 */
const viewport = { innerHeight: 800, scrollHeight: 5000 };

/** 装好全部滚动相关的桩，返回可断言的 `scrollTo` */
function stubScrolling(initialY: number) {
  const scrollTo = vi.fn();
  Object.defineProperty(window, 'scrollY', { value: initialY, configurable: true, writable: true });
  Object.defineProperty(window, 'scrollTo', { value: scrollTo, configurable: true });
  Object.defineProperty(window, 'innerHeight', {
    value: viewport.innerHeight,
    configurable: true,
  });
  Object.defineProperty(document.documentElement, 'scrollHeight', {
    value: viewport.scrollHeight,
    configurable: true,
  });
  return scrollTo;
}

/** 让"文档高度"变化，并手动触发观察者回调（jsdom 不会自己触发） */
let resizeCallbacks: (() => void)[] = [];
function setScrollHeight(next: number) {
  viewport.scrollHeight = next;
  Object.defineProperty(document.documentElement, 'scrollHeight', {
    value: next,
    configurable: true,
  });
  for (const cb of resizeCallbacks) cb();
}

/** 走完当前排队的定时器（`requestAnimationFrame` 的替代实现用的是 16ms 定时器） */
async function flushFrames() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 40));
  });
}

interface HarnessProps {
  noteId?: string | undefined;
  enabled?: boolean;
  mode?: string;
  skipRestore?: boolean;
  /** 正文容器是否已挂载。`false` 模拟"数据还没回来、正文还没渲染" */
  renderContainer?: boolean;
}

/**
 * 被挂载的测试宿主。
 *
 * `containerRef` 与真实页面同形：ref 在页面层持有、指向正文容器
 * （`pages/NoteDetail.tsx:66` 的 `markdownRef`），容器存在 = 正文已渲染。
 */
function Harness({
  noteId = 'note-1',
  enabled = true,
  mode = 'clean',
  skipRestore = false,
  renderContainer = true,
}: HarnessProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  useScrollMemory({ noteId, containerRef, enabled, mode, skipRestore });
  return (
    <div>
      {renderContainer ? <div ref={containerRef} data-testid="markdown-body" /> : null}
      <span>宿主</span>
    </div>
  );
}

/** 模拟用户滚动到某处（写 `scrollY` 再派发 scroll 事件） */
function userScrollsTo(y: number) {
  Object.defineProperty(window, 'scrollY', { value: y, configurable: true, writable: true });
  act(() => {
    window.dispatchEvent(new Event('scroll'));
  });
}

beforeEach(() => {
  localStorage.clear();
  viewport.scrollHeight = 5000;
  resizeCallbacks = [];
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(NOW);
  // jsdom 默认没有 ResizeObserver（与 GraphCanvas.tsx:129-136 记录的同一个事实）
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(cb: () => void) {
        resizeCallbacks.push(cb);
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  );
  // jsdom 默认没有 requestAnimationFrame（vite.config.ts 未开 pretendToBeVisual）
  vi.stubGlobal('requestAnimationFrame', undefined);
  vi.stubGlobal('cancelAnimationFrame', undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('恢复位置（刷新后回到原处）', () => {
  it('★ 卸载再挂载（= 刷新）后滚回上次的位置', async () => {
    writeScrollMemory('note-1', 1800, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness />);
    await flushFrames();

    expect(scrollTo).toHaveBeenCalledWith({ top: 1800, behavior: 'instant' });
  });

  it('★ 只滚一次（重试循环必须停下来，不能反复打断用户）', async () => {
    writeScrollMemory('note-1', 1800, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness />);
    await flushFrames();
    await flushFrames();
    await flushFrames();

    expect(scrollTo).toHaveBeenCalledTimes(1);
  });

  it('没有记忆时不滚（首次打开就是顶部，不该有任何跳动）', async () => {
    const scrollTo = stubScrolling(0);
    render(<Harness />);
    await flushFrames();
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('记忆位置就在顶部附近时不滚（不为几像素制造一次跳动）', async () => {
    writeScrollMemory('note-1', 3, 'clean', NOW);
    const scrollTo = stubScrolling(0);
    render(<Harness />);
    await flushFrames();
    expect(scrollTo).not.toHaveBeenCalled();
  });
});

describe('时机：正文是异步撑开高度的', () => {
  it('★ 文档高度还不够时不滚，等高度上来才滚（避免"滚了一半"）', async () => {
    writeScrollMemory('note-1', 4000, 'clean', NOW);
    // 正文还没渲染：文档只有 1000px 高，滚到 4000 会被钳住
    viewport.scrollHeight = 1000;
    const scrollTo = stubScrolling(0);

    render(<Harness />);
    await flushFrames();
    expect(scrollTo).not.toHaveBeenCalled();

    // 正文渲染完，高度上来了 —— 观察者触发重试
    act(() => setScrollHeight(6000));
    await flushFrames();

    expect(scrollTo).toHaveBeenCalledWith({ top: 4000, behavior: 'instant' });
  });

  it('★ 正文容器还没挂载时不滚（正文存在才谈得上位置）', async () => {
    writeScrollMemory('note-1', 4000, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness renderContainer={false} />);
    await flushFrames();
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('★ 一直等不到足够高度就放弃并保持顶部（有界重试，不无限等）', async () => {
    writeScrollMemory('note-1', 999_999, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness />);
    // 跑过 1.5s 的重试上限
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1700));
    });

    expect(scrollTo).not.toHaveBeenCalled();
  });
});

describe('三个"不恢复"的分支', () => {
  it('★ 引用回跳时不恢复：滚动让给 citationJump，两个滚动不能打架', async () => {
    writeScrollMemory('note-1', 1800, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness skipRestore />);
    await flushFrames();

    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('★ 视图模式不一致时不恢复：原始版与清洗版的段落位置没有可比性', async () => {
    // 上次读的是清洗版，这次打开的是原始版
    writeScrollMemory('note-1', 1800, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness mode="original" />);
    await flushFrames();

    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('★ 用户自己先滚了就不抢：主动滚走说明他不想接着上次看', async () => {
    writeScrollMemory('note-1', 4000, 'clean', NOW);
    // 高度不够 ⇒ 不会立刻滚，重试循环还在跑
    viewport.scrollHeight = 1000;
    const scrollTo = stubScrolling(0);

    render(<Harness />);
    await flushFrames();

    // 用户在重试期间自己滚到 500
    userScrollsTo(500);
    // 然后高度上来了
    act(() => setScrollHeight(6000));
    await flushFrames();

    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('非阅读状态（编辑态 / diff 态）既不恢复也不记录', async () => {
    writeScrollMemory('note-1', 1800, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<Harness enabled={false} />);
    await flushFrames();
    expect(scrollTo).not.toHaveBeenCalled();

    userScrollsTo(2500);
    await flushFrames();
    // 位置没有被覆盖：记忆里还是上次那份
    expect(readScrollMemory('note-1', NOW)?.y).toBe(1800);
  });
});

describe('保存位置', () => {
  it('★ 滚动停下来之后落盘', async () => {
    stubScrolling(0);
    render(<Harness />);
    await flushFrames();

    userScrollsTo(1234);
    // 节流窗口 200ms
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 260));
    });

    expect(readScrollMemory('note-1', NOW)?.y).toBe(1234);
    expect(readScrollMemory('note-1', NOW)?.mode).toBe('clean');
  });

  it('★ 滚一下立刻按 F5 也不丢：pagehide 兜底写，不等节流窗口', async () => {
    stubScrolling(0);
    render(<Harness />);
    await flushFrames();

    userScrollsTo(2345);
    // 不等 200ms 的节流窗口，直接模拟刷新/关闭
    act(() => {
      window.dispatchEvent(new Event('pagehide'));
    });

    expect(readScrollMemory('note-1', NOW)?.y).toBe(2345);
  });

  it('节流窗口内的连续滚动只落最后那个位置', async () => {
    stubScrolling(0);
    render(<Harness />);
    await flushFrames();

    userScrollsTo(100);
    userScrollsTo(200);
    userScrollsTo(300);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 260));
    });

    expect(readScrollMemory('note-1', NOW)?.y).toBe(300);
  });

  it('卸载后不再记录（监听器必须解绑，否则切走后还在写）', async () => {
    stubScrolling(0);
    const view = render(<Harness />);
    await flushFrames();
    userScrollsTo(500);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 260));
    });
    expect(readScrollMemory('note-1', NOW)?.y).toBe(500);

    view.unmount();
    userScrollsTo(9999);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 260));
    });

    expect(readScrollMemory('note-1', NOW)?.y).toBe(500);
  });

  it('换笔记时重新读记忆，不把上一篇的位置带过去', async () => {
    writeScrollMemory('note-a', 1000, 'clean', NOW);
    writeScrollMemory('note-b', 2500, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    const view = render(<Harness noteId="note-a" />);
    await flushFrames();
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 1000, behavior: 'instant' });

    view.rerender(<Harness noteId="note-b" />);
    await flushFrames();
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 2500, behavior: 'instant' });
  });
});

describe('与既有行为的隔离', () => {
  it('宿主渲染不受影响（hook 不返回任何东西、不改变 DOM）', async () => {
    stubScrolling(0);
    render(<Harness />);
    await flushFrames();
    expect(screen.getByTestId('markdown-body')).toBeInTheDocument();
  });

  it('localStorage 不可用时静默降级：不抛错、不滚动', async () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('隐私模式');
    });
    const scrollTo = stubScrolling(0);

    expect(() => render(<Harness />)).not.toThrow();
    await flushFrames();
    expect(scrollTo).not.toHaveBeenCalled();

    getItem.mockRestore();
  });
});

/** 一个用到 `useState` 的宿主，确保"多次渲染"这条路径也被覆盖 */
function StatefulHarness() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);
  useScrollMemory({
    noteId: 'note-1',
    containerRef,
    enabled: true,
    mode: 'clean',
    skipRestore: false,
  });
  return (
    <div>
      <div ref={containerRef} />
      <button type="button" onClick={() => setTick((t) => t + 1)}>
        重渲染 {tick}
      </button>
    </div>
  );
}

describe('重渲染不会重复恢复', () => {
  it('★ 同一篇笔记的多次重渲染只滚一次', async () => {
    writeScrollMemory('note-1', 1800, 'clean', NOW);
    const scrollTo = stubScrolling(0);

    render(<StatefulHarness />);
    await flushFrames();
    expect(scrollTo).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole('button').click();
      screen.getByRole('button').click();
    });
    await flushFrames();

    expect(scrollTo).toHaveBeenCalledTimes(1);
  });
});
