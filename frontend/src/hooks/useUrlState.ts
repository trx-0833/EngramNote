/**
 * @file 把一段 UI 状态挂到 URL 查询串上（2026-11-10）
 *
 * ## 为什么需要它
 *
 * 列表页的筛选 / 页码 / Tab 原先都是 `useState`。用户从「笔记列表」点进一篇笔记、
 * 再点返回时，列表会**重新挂载**：筛选回默认、回到第 1 页、滚回顶部 ——
 * 他得重新定位一遍。这正是本次要减少的时间。
 *
 * 把这些状态放进 URL 之后，三件事同时成立：
 *
 * | 场景 | 结果 |
 * |---|---|
 * | 详情页「返回列表」走 `navigate(-1)` | 浏览器回到**带参数的那个 URL**，筛选与页码原样 |
 * | 刷新页面 / 收藏夹 | 同一个视图（不再是"总是回到默认筛选"） |
 * | 复制链接给别人 | 对方看到同一份筛选 |
 *
 * ## 为什么用 `replace: true`
 *
 * 筛选与翻页**不产生历史条目**：否则用户在筛选器上点三下，就要按三次"后退"
 * 才能离开列表页 —— 那是比"回不到原位置"更烦人的体验。
 * 需要保留的"前进/后退"粒度仍然是**页面级**的（列表 ↔ 详情）。
 *
 * ## 为什么默认值不写进 URL
 *
 * `?role=material` 与没有这个参数是同一个视图；默认值写进 URL 只会让地址栏变长、
 * 让"分享出去的链接"看起来像带了一堆无意义参数。读取时缺省即默认值。
 */
import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/**
 * 一次写入多个 URL 状态（**一次导航**）
 *
 * ## 为什么需要"一次"
 *
 * `setSearchParams` 的每次调用都是一次导航。同一 tick 里连着调两次
 * （例如"改关键词"顺手把页码重置成 1），第二次拿到的 `prev` 是**尚未提交**的
 * 那一份，于是第一次写进去的 `q` 被覆盖掉 —— 表现是"搜索框打字了但列表没变"。
 * 这个坑在 `NotesList` 上真的踩到了（测试 `★★ 搜索状态下不显示导出全部`）。
 *
 * 所以筛选项的更新统一走这里：想改几个字段就传几个，合并成一次导航。
 *
 * @returns `patch`：把给定的键写进 URL；值为 `''` 表示**删除**该键
 *   （对应"回到了默认值"，见文件头"默认值不写进 URL"）
 */
export function useUrlPatch(): (next: Record<string, string>) => void {
  const [, setParams] = useSearchParams();
  return useCallback(
    (next: Record<string, string>) => {
      setParams(
        (prev) => {
          const draft = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(next)) {
            if (value === '') draft.delete(key);
            else draft.set(key, value);
          }
          return draft;
        },
        { replace: true },
      );
    },
    [setParams],
  );
}

/** 读取一个字符串型 URL 状态；缺省时返回 `defaultValue` */
export function useUrlState(key: string, defaultValue: string): [string, (next: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? defaultValue;

  const set = useCallback(
    (next: string) => {
      setParams(
        (prev) => {
          const draft = new URLSearchParams(prev);
          if (next === defaultValue) {
            draft.delete(key); // 默认值不落进 URL，见文件头
          } else {
            draft.set(key, next);
          }
          return draft;
        },
        // `replace`：筛选不产生历史条目（否则后退要按很多次）
        { replace: true },
      );
    },
    [key, defaultValue, setParams],
  );

  return [value, set];
}

/** 读取一个数字型 URL 状态（非法值 / 缺省时返回 `defaultValue`） */
export function useUrlNumber(
  key: string,
  defaultValue: number,
  /** 合法下界（含）。用于页码这类"1 起"的字段 */
  min = 1,
): [number, (next: number) => void] {
  const [raw, setRaw] = useUrlState(key, String(defaultValue));
  const parsed = Number.parseInt(raw, 10);
  const value = Number.isFinite(parsed) && parsed >= min ? parsed : defaultValue;

  const set = useCallback(
    (next: number) => {
      setRaw(String(Math.max(min, Math.trunc(next))));
    },
    [setRaw, min],
  );

  return [value, set];
}

/** 读取一个布尔型 URL 状态（`1` 为真，缺省为 `defaultValue`） */
export function useUrlFlag(key: string, defaultValue = false): [boolean, (next: boolean) => void] {
  const [raw, setRaw] = useUrlState(key, defaultValue ? '1' : '0');
  return [raw === '1', useCallback((next: boolean) => setRaw(next ? '1' : '0'), [setRaw])];
}
