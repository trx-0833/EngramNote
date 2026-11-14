import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useThrottledStream } from './useStreamAnswer';

/**
 * `useThrottledStream` —— 流式增量的节流累积器
 *
 * ## 这里守的是什么（真实事故，2026-11-14）
 *
 * 原实现在提交后**没有清空缓冲**：
 *
 * ```js
 * bufferRef.current += chunk;
 * timer = setTimeout(() => { if (bufferRef.current) commit(bufferRef.current); }, 80);
 * ```
 *
 * 于是第 k 次提交追加的是"从本轮开始到现在的全部文本"，而不是"新增的那一段"：
 * 答案变成 `A` + `AB` + `ABC` + … 的拼接 —— 用户看到一大段连续重复的内容，
 * 而且随回答变长呈平方级膨胀。追问时答案更长、跨的节流窗口更多，最明显。
 *
 * 更隐蔽的一层：`done` 事件会 `flush()` 一次、流自然结束后还会再 `flush()` 一次
 * （QA.tsx 两处），缓冲不清空时这两次会把**同一整段**再各追加一遍 —— 于是哪怕
 * 回答短到只装进一个窗口，答案也至少重复三遍。
 *
 * 测试之所以一直是绿的：假 SSE 流一次性把事件派发完，80ms 定时器根本没机会触发，
 * 只剩两次 flush；而断言用的是 `findByText(/…/)` 这类正则，重复几遍都能匹配。
 */
describe('useThrottledStream：提交后必须清空缓冲', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('多次 push 只把**新增**文本提交一次（不重复累积）', () => {
    const { result } = renderHook(() => useThrottledStream(80));
    const committed: string[] = [];
    const commit = (text: string) => committed.push(text);

    act(() => {
      result.current.push('浮充', commit);
      result.current.push('是恒压', commit);
      result.current.push('充电。', commit);
    });

    // 定时器还没到，什么都不该提交
    expect(committed).toEqual([]);

    act(() => {
      vi.advanceTimersByTime(80);
    });
    expect(committed).toEqual(['浮充是恒压充电。']);

    // 第二个窗口：只有新增的那一段
    act(() => {
      result.current.push('不', commit);
      result.current.push('同。', commit);
      vi.advanceTimersByTime(80);
    });
    expect(committed).toEqual(['浮充是恒压充电。', '不同。']);

    // 拼起来恰好等于全部 token，一次不多一次不少
    expect(committed.join('')).toBe('浮充是恒压充电。不同。');
  });

  it('连续两次 flush（done + 流自然结束）不会把同一段提交两遍', () => {
    const { result } = renderHook(() => useThrottledStream(80));
    const committed: string[] = [];
    const commit = (text: string) => committed.push(text);

    act(() => {
      result.current.push('半截答案', commit);
    });

    // QA.tsx 的真实形状：done 事件 flush 一次，流结束后再 flush 一次
    act(() => {
      result.current.flush(commit);
    });
    act(() => {
      result.current.flush(commit);
    });

    expect(committed).toEqual(['半截答案']);
  });

  it('flush 会取消未触发的定时器，避免"提交两次"', () => {
    const { result } = renderHook(() => useThrottledStream(80));
    const committed: string[] = [];
    const commit = (text: string) => committed.push(text);

    act(() => {
      result.current.push('abc', commit);
      result.current.flush(commit);
    });
    // 定时器本该在 80ms 后提交，但 flush 已经把它清掉且缓冲已空
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(committed).toEqual(['abc']);
  });

  it('reset 之后新的一轮从空缓冲开始（不把上一轮残余带进来）', () => {
    const { result } = renderHook(() => useThrottledStream(80));
    const committed: string[] = [];
    const commit = (text: string) => committed.push(text);

    act(() => {
      result.current.push('上一轮的尾巴', commit);
      result.current.reset();
      result.current.push('新的一轮', commit);
      vi.advanceTimersByTime(80);
    });

    expect(committed).toEqual(['新的一轮']);
  });
});
