/**
 * @file 「返回列表」的正确做法（2026-11-10）
 *
 * ## 问题
 *
 * 详情页的返回按钮原先一律 `navigate('/notes')`（或 `/cards`）。那是一次
 * **PUSH** 到一条**全新**的地址：列表页重新挂载、筛选与页码回到默认、
 * 滚动回到顶部 —— 用户得重新找刚才看的那一条。
 *
 * 而"我是从列表点进来的"这个信息本来就存在浏览器历史里：后退一步就回到
 * **带参数的那个 URL**。所以返回按钮应当是"后退"，fallback 才是"打开列表"。
 *
 * ## 为什么不是无脑 `navigate(-1)`
 *
 * 直接打开详情链接（收藏、别人发的链接、刷新后）时，历史里**没有**上一条
 * 页面 —— `navigate(-1)` 会把用户留在原地或跳去浏览器之外。判据是
 * react-router 写在 `history.state.idx` 里的历史深度：
 *
 * | `idx` | 含义 | 动作 |
 * |---|---|---|
 * | `> 0` | 本次会话里有上一页 | `navigate(-1)`：回到原位置 |
 * | `0` / 缺失 | 直接进入（深链接 / 新标签页） | `navigate(fallback, { replace: true })` |
 *
 * `replace` 用于 fallback：给深链接补一条"列表"历史，而不是在历史里塞两条。
 *
 * ## 与缓存的关系
 *
 * 后退回到列表时，react-query 命中缓存（列表数据仍在，`staleTime` 内不会重取），
 * 滚动位置由 `useListScrollRestore` 恢复，URL 带回了筛选 —— 三者合起来才是
 * "回到原来的位置"。
 */
import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { readListUrl } from '../utils/listReturnMemory';

/** react-router 放在 `history.state.idx` 里的历史深度（0 起） */
interface RouterHistoryState {
  idx?: number;
}

/**
 * 该不该用"后退"而不是"打开列表"（纯函数，便于直接测）
 *
 * @param depth - `history.state.idx`；缺失或 0 表示没有上一页
 */
export function shouldGoBack(depth: number | undefined): boolean {
  return typeof depth === 'number' && depth > 0;
}

/**
 * 返回列表：能后退就后退（保留原 URL 与滚动），否则打开 fallback
 *
 * @param fallback - 没有上一页时的落地路由，例如 `/notes`
 */
export function useBackToList(fallback: string): () => void {
  const navigate = useNavigate();
  return useCallback(() => {
    const state = (window.history.state ?? null) as RouterHistoryState | null;
    if (shouldGoBack(state?.idx)) {
      navigate(-1);
      return;
    }
    // 没有历史深度（深链接直达 / history.state 被重写）：优先回到"列表上次停的 URL"，
    // 否则连筛选与页码一起丢 —— 那正是这个改动要消灭的体验。
    const remembered = readListUrl(fallback.replace(/^\//, ''));
    navigate(remembered ?? fallback, { replace: true });
  }, [navigate, fallback]);
}
