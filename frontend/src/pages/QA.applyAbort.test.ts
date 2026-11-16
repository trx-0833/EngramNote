// 构建期把源码作为字符串导进来（不依赖运行时 cwd）。
// 原来用 readFileSync，在 CI 上因 cwd/root 与本地不同而 ENOENT，
// 表现就是「本地绿、CI 红」。
const rawSources = import.meta.glob('./QA.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

import { describe, expect, it } from 'vitest';

import { applyAbort } from './QA';

describe('applyAbort（round5：守住 abort 分支的接线）', () => {
  it('done 已到（有 id）的轮次保持 ok，不改写成 aborted', () => {
    const prev = [{ id: 'turn-1', status: 'ok', question: 'q', answer: 'a' }];
    expect(applyAbort(prev)[0].status).toBe('ok');
  });

  it('done 未到的轮次记成 aborted', () => {
    const prev = [{ status: undefined, question: 'q', answer: 'a' }];
    expect(applyAbort(prev)[0].status).toBe('aborted');
  });

  it('状态没变化时返回同一个数组（避免无谓渲染）', () => {
    const prev = [{ id: 'turn-1', status: 'ok' }];
    expect(applyAbort(prev)).toBe(prev);
  });

  it('空列表原样返回', () => {
    const prev: { id?: string; status?: string }[] = [];
    expect(applyAbort(prev)).toBe(prev);
  });
});

// round6：abort 分支的**接线**（jsdom 无法模拟"流结束后才 abort"，
// 硬写会得到恒真用例 —— A8 踩过）。这里退一步用源码断言，
// 至少能抓住"把调用点换回内联逻辑"这类变异。
it('abort 分支必须调用 applyAbort（源码断言）', () => {
  const raw = rawSources['./QA.tsx'];
  expect(typeof raw).toBe('string');
  expect(raw).toContain('setHistory(applyAbort)');
});
