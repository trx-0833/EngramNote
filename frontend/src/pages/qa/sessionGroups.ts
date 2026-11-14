import type { QASessionSummary } from '../../api/qaHistory';

/** 时间分组：与 DeepSeek 网页版一致的「今天 / 7 天内 / 30 天内 / 更早」 */
export type SessionGroupKey = 'today' | 'week' | 'month' | 'older';

export interface SessionGroup {
  key: SessionGroupKey;
  /** 该组的会话，保持传入顺序（接口已按 `updated_at desc` 排好） */
  items: QASessionSummary[];
}

/**
 * 把会话列表按「最后活跃时间」分组
 *
 * ## 为什么在前端分（而不是后端返回分组）
 *
 * 分组只依赖 `updated_at`，而这一列本来就随列表返回。放进接口意味着：
 * 后端要按"用户所在时区"切当天的边界 —— 服务器时区与用户时区不一致时
 * 「今天」会错位；而浏览器知道用户真实的本地时间。**零后端改动**也顺带少一个契约。
 *
 * ## 边界
 *
 * - 用**日历天**而不是 24 小时窗口：凌晨 1 点看的"今天"应该是今天，
 *   不是"过去 24 小时"（后者会把昨晚 23:50 的会话也算进来）。
 * - `updated_at` 解析失败（脏数据 / 空串）的条目归到 `older`，不丢、不抛错 ——
 *   列表里宁可位置难看，也不能整页崩掉。
 * - 空输入返回空数组（不返回四个空组），调用方据此显示"还没有对话"。
 */
export function groupSessions(
  sessions: QASessionSummary[],
  now: Date = new Date(),
): SessionGroup[] {
  if (sessions.length === 0) return [];

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const dayMs = 24 * 60 * 60 * 1000;

  const buckets: Record<SessionGroupKey, QASessionSummary[]> = {
    today: [],
    week: [],
    month: [],
    older: [],
  };

  for (const session of sessions) {
    const raw = session.updated_at;
    const ts = raw ? Date.parse(raw) : Number.NaN;
    if (Number.isNaN(ts)) {
      buckets.older.push(session);
      continue;
    }
    if (ts >= startOfToday) {
      buckets.today.push(session);
    } else if (ts >= startOfToday - 7 * dayMs) {
      // 「7 天内」= 今天之前、最近 7 个日历天（与 DeepSeek 的措辞一致）
      buckets.week.push(session);
    } else if (ts >= startOfToday - 30 * dayMs) {
      buckets.month.push(session);
    } else {
      buckets.older.push(session);
    }
  }

  return (['today', 'week', 'month', 'older'] as const)
    .filter((key) => buckets[key].length > 0)
    .map((key) => ({ key, items: buckets[key] }));
}
