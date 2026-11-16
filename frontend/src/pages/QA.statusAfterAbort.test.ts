import { describe, expect, it } from 'vitest';

import { statusAfterAbort } from './QA';

describe('statusAfterAbort（round4 m4）', () => {
  it('done 已到（有 id）时保持原状态，不改写成 aborted', () => {
    expect(statusAfterAbort({ id: 'turn-1', status: 'ok' })).toBe('ok');
  });

  it('done 未到（没有 id）时记成 aborted', () => {
    expect(statusAfterAbort({ status: undefined })).toBe('aborted');
  });
});
