import { describe, expect, it } from 'vitest';

import { citationAudit, citedSourceIndexes } from './QA';

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

describe('citationAudit（A1：引用结构校验）', () => {
  it('标出越界编号', () => {
    const r = citationAudit('甲[1]，乙[5]，丙[0]。', 3);
    expect(r.invalid).toEqual([5, 0]);
    expect([...r.cited]).toEqual([1]);
  });

  it('正常答案零误报（报告验收硬指标）', () => {
    const r = citationAudit('甲说[1]。乙说[2]。', 2);
    expect(r.invalid).toEqual([]);
    expect(r.cited.size).toBe(2);
    expect(r.coverage).toBe(1);
  });

  it('覆盖率 = 含引用的句子 / 总句子', () => {
    const r = citationAudit('有引用[1]。没有引用。还有一条[2]。', 2);
    expect(r.coverage).toBeCloseTo(2 / 3, 5);
  });

  it('空答案不产生 NaN', () => {
    const r = citationAudit('', 3);
    expect(r.coverage).toBe(0);
    expect(r.invalid).toEqual([]);
  });
});
