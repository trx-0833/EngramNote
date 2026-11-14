import { describe, expect, it } from 'vitest';
import type { QASessionSummary } from '../../api/qaHistory';
import { groupSessions } from './sessionGroups';

function session(id: string, updatedAt: string): QASessionSummary {
  return {
    id,
    title: id,
    last_question: null,
    surface: 'global',
    turn_count: 1,
    created_at: updatedAt,
    updated_at: updatedAt,
  } as QASessionSummary;
}

/** 固定"现在"：2026-11-14 21:00 本地时间，避免用例随运行时间漂移 */
const NOW = new Date(2026, 10, 14, 21, 0, 0);

describe('groupSessions：按日历天分组', () => {
  it('按今天 / 7 天内 / 30 天内 / 更早切开，且保持传入顺序', () => {
    const groups = groupSessions(
      [
        session('a', new Date(2026, 10, 14, 9, 0).toISOString()), // 今天
        session('b', new Date(2026, 10, 12, 9, 0).toISOString()), // 2 天前
        session('c', new Date(2026, 9, 30, 9, 0).toISOString()), // 15 天前
        session('d', new Date(2026, 7, 1, 9, 0).toISOString()), // 3 个月前
      ],
      NOW,
    );

    expect(groups.map((g) => g.key)).toEqual(['today', 'week', 'month', 'older']);
    expect(groups[0].items.map((s) => s.id)).toEqual(['a']);
    expect(groups[1].items.map((s) => s.id)).toEqual(['b']);
    expect(groups[2].items.map((s) => s.id)).toEqual(['c']);
    expect(groups[3].items.map((s) => s.id)).toEqual(['d']);
  });

  it('空组不返回（调用方据此显示"还没有对话"）', () => {
    const groups = groupSessions([session('a', NOW.toISOString())], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('today');
  });

  it('用日历天而不是 24 小时窗口：昨天 23:50 不算"今天"', () => {
    const groups = groupSessions(
      [session('late', new Date(2026, 10, 13, 23, 50).toISOString())],
      new Date(2026, 10, 14, 0, 30, 0),
    );
    expect(groups[0].key).toBe('week');
  });

  it('`updated_at` 解析不了时归到更早，不抛错也不丢条目', () => {
    const broken = { ...session('x', ''), updated_at: 'not-a-date' } as QASessionSummary;
    const groups = groupSessions([broken], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('older');
    expect(groups[0].items[0].id).toBe('x');
  });

  it('空输入返回空数组', () => {
    expect(groupSessions([], NOW)).toEqual([]);
  });
});
