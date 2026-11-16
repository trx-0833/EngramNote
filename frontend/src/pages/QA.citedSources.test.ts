import { describe, expect, it } from 'vitest';

import { citedSourceIndexes } from './QA';

describe('citedSourceIndexes（A3：标出未被引用的来源）', () => {
  it('解析出正文里引用到的编号', () => {
    expect([...citedSourceIndexes('甲说[1]，乙说[3]。', 3)]).toEqual([1, 3]);
  });

  it('忽略了越界编号（越界属于 A1 的结构校验，本轮不做）', () => {
    expect([...citedSourceIndexes('引用[9]与[0]', 3)]).toEqual([]);
  });

  it('重复引用只记一次', () => {
    expect([...citedSourceIndexes('[2] [2] [2]', 2)]).toEqual([2]);
  });

  it('没有引用时返回空集合（右栏会把全部标成未被引用）', () => {
    expect(citedSourceIndexes('完全没有引用', 2).size).toBe(0);
  });
});
