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
