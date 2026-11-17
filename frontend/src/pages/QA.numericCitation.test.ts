/**
 * C1 降级版（纯规则）的用例：数字能不能在引用原文里找到。
 *
 * 核心是**三种结果要分清**：
 * - 找到 ⇒ 通过；
 * - 找不到 且 文本完整（< 200 字）⇒ `unsupported`（硬错，要报）；
 * - 找不到 但 文本可能被截断（≥ 200 字）⇒ `unverifiable`（不报，避免误伤）。
 */
import { describe, expect, it } from 'vitest';

import { numericCitationAudit } from './QA';

function src(relevant_text: string) {
  return {
    note_id: 'n1',
    note_title: '笔记',
    relevant_text,
  } as never;
}

describe('numericCitationAudit（C1 降级版）', () => {
  it('数字在引用原文里 → 通过', () => {
    const r = numericCitationAudit('水导轴承共 8 块瓦[1]。', [src('水导轴承由 8 块瓦组成')]);
    expect(r.checked).toBe(1);
    expect(r.unsupported).toHaveLength(0);
    expect(r.unverifiable).toBe(0);
  });

  it('数字找不到且原文完整（< 200 字）→ unsupported', () => {
    const r = numericCitationAudit('水导轴承共 12 块瓦[1]。', [src('水导轴承由 8 块瓦组成')]);
    expect(r.unsupported).toHaveLength(1);
    expect(r.unsupported[0].numbers).toEqual(['12']);
  });

  it('数字找不到但原文可能被截断（≥ 200 字）→ unverifiable，不报', () => {
    const long = '水导轴承'.repeat(60); // > 200 字，且不含 12
    const r = numericCitationAudit('水导轴承共 12 块瓦[1]。', [src(long)]);
    expect(r.unsupported).toHaveLength(0);
    expect(r.unverifiable).toBe(1);
  });

  it('句子没标引用时用全部来源兜底（不把"忘标引用"当"编造"）', () => {
    const r = numericCitationAudit('共 8 块瓦。', [src('由 8 块瓦组成')]);
    expect(r.unsupported).toHaveLength(0);
  });

  it('没有数字的句子不计入 checked', () => {
    const r = numericCitationAudit('水导轴承由多块瓦组成。', [src('8 块')]);
    expect(r.checked).toBe(0);
  });

  it('小数与百分号也能识别', () => {
    const r = numericCitationAudit('油温上限 45.5 度[1]，报警值 80%[1]。', [src('油温上限 40 度')]);
    expect(r.unsupported).toHaveLength(1);
    expect(r.unsupported[0].numbers).toContain('45.5');
  });

  it('来源没有可读文本时记 unverifiable，不报硬错', () => {
    const r = numericCitationAudit('共 8 块瓦[1]。', [src('')]);
    expect(r.unsupported).toHaveLength(0);
    expect(r.unverifiable).toBe(1);
  });
});
