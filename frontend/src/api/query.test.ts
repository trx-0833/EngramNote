/**
 * @file `buildQuery` 的语义锁（阶段 5.1 / S3b）
 *
 * 这些用例不是"覆盖率"，而是**迁移等价性的契约**：18 个调用点从手拼查询串
 * 换成 `buildQuery` 之后，"发出去的 URL 是否逐字一致"全靠这里锁住。
 * 其中最容易悄悄改掉的三条单独点名：
 *
 * - **键序**：`?page=1&page_size=20&keyword=x` 与换序后的串在语义上等价，
 *   但 `git diff` 与 e2e 的桩断言都按字面看，所以顺序是契约；
 * - **空值跳过**：`undefined` / `null` / `''` 必须**不产生参数**（迁移前的
 *   `if (keyword)` 就是这个行为）；
 * - **`false` 照发**：跳过 `false` 会变成一条隐式规则，"不想要 false"的地方
 *   必须在调用点显式写出来。
 */
import { describe, expect, it } from 'vitest';

import { buildQuery } from './query';

describe('buildQuery', () => {
  it('空对象返回空串（不是 "?"）', () => {
    expect(buildQuery({})).toBe('');
  });

  it('键序 = 书写顺序', () => {
    expect(buildQuery({ page: 1, page_size: 20, keyword: 'x' })).toBe(
      '?page=1&page_size=20&keyword=x',
    );
    expect(buildQuery({ keyword: 'x', page: 1 })).toBe('?keyword=x&page=1');
  });

  it('跳过 undefined / null / 空串，但保留 false / 0', () => {
    expect(buildQuery({ a: undefined, b: null, c: '', d: 'keep' })).toBe('?d=keep');
    expect(buildQuery({ a: false, b: 0 })).toBe('?a=false&b=0');
  });

  it('数字与布尔直接 String()', () => {
    expect(buildQuery({ limit: 10, promote: true })).toBe('?limit=10&promote=true');
  });

  it('数组 → 重复键，元素同样过滤空值', () => {
    expect(buildQuery({ ids: ['a', 'b'] })).toBe('?ids=a&ids=b');
    expect(buildQuery({ ids: ['a', null, '', 'b', undefined] })).toBe('?ids=a&ids=b');
    expect(buildQuery({ ids: [] })).toBe('');
  });

  it('编码按 form 规则：空格 → +、字面 + → %2B、中文按 UTF-8', () => {
    expect(buildQuery({ q: 'a b' })).toBe('?q=a+b');
    expect(buildQuery({ q: 'a+b' })).toBe('?q=a%2Bb');
    expect(buildQuery({ q: '中' })).toBe('?q=%E4%B8%AD');
    expect(buildQuery({ q: 'a&b=c' })).toBe('?q=a%26b%3Dc');
  });

  it('不支持的取值类型当场抛 TypeError（不静默发 [object Object]）', () => {
    expect(() => buildQuery({ o: {} })).toThrow(TypeError);
    expect(() => buildQuery({ o: { nested: 1 } })).toThrow(/不支持/);
    expect(() => buildQuery({ f: () => 1 })).toThrow(TypeError);
  });

  it('值语义与今天的手拼形态一致（三个真实调用点的回归样本）', () => {
    // notes.getNotes：page/page_size 恒定，keyword/note_role 有则加
    expect(buildQuery({ page: 1, page_size: 20, keyword: undefined, note_role: 'material' })).toBe(
      '?page=1&page_size=20&note_role=material',
    );
    // notes.purgeNote：今天写的是 `promoteKeyCards ? '?promote_key_cards=true' : ''`
    // —— `false` 时**不发**这个参数，所以在调用点显式写成 `x || undefined`
    // （`buildQuery` 自己是"false 照发"的通用语义，见上一条）
    const promote = (value: boolean) => value || undefined;
    expect(buildQuery({ promote_key_cards: promote(true) })).toBe('?promote_key_cards=true');
    expect(buildQuery({ promote_key_cards: promote(false) })).toBe('');
    // notes.diffVersions：`?v1=${v1}&v2=${v2}`
    expect(buildQuery({ v1: 1, v2: 3 })).toBe('?v1=1&v2=3');
  });
});
