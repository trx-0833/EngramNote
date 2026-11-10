/**
 * @file 「列表上次停在哪」—— 记住列表的**完整 URL**（2026-11-10）
 *
 * ## 为什么光有 `history.state.idx` 不够
 *
 * `useBackToList` 的第一版靠 react-router 写在 `history.state.idx` 里的历史深度
 * 判断"能不能后退"。这在正常点击流程里没问题，但有两类入口 `idx` 是缺失的：
 *
 * - 应用内某处用 `window.history.pushState({}, '', url)` 直接改地址（e2e 的登录
 *   夹具就这么干，实测踩到）；
 * - 少数浏览器/扩展在恢复会话时重写 history state。
 *
 * 一旦 `idx` 缺失，后退分支就用不上，只能 `navigate(fallback)` —— 而 fallback
 * 是个**裸路径**，于是筛选与页码全丢。用户看到的正是"返回后又得重新筛一遍"。
 *
 * ## 所以再记一份"列表 URL"
 *
 * 列表页卸载时把 `pathname + search` 存进 `sessionStorage`；详情页返回时若
 * 无法后退，就回到**这个 URL**（而不是裸路径）。两者配合：
 *
 * | 情况 | 结果 |
 * |---|---|
 * | 有历史深度 | `navigate(-1)`：连滚动位置一起回来（POP） |
 * | 无历史深度 | `navigate(记住的 URL)`：筛选与页码回来（PUSH，滚动回顶部） |
 * | 都没有（第一次直接进详情） | `navigate(fallback)` |
 *
 * 用 `sessionStorage`：关掉标签页就忘（与 `scrollMemory` 的长留存不同 ——
 * 那是"接着上次读"，这是"这次浏览的连续性"）。
 */
const PREFIX = 'engramnote:listUrl:';

/** 记下某个列表当前的完整 URL（通常在列表卸载时调用） */
export function rememberListUrl(key: string, url: string): void {
  try {
    sessionStorage.setItem(PREFIX + key, url);
  } catch {
    // 隐私模式 / 存储被禁用：不影响页面
  }
}

/** 读取某个列表上次的 URL；没有记忆时返回 `null` */
export function readListUrl(key: string): string | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    // 只接受站内路径：防止被塞进 `javascript:` 之类的东西
    return raw && raw.startsWith('/') ? raw : null;
  } catch {
    return null;
  }
}

/** 清掉某个列表的记忆（列表被清空 / 用户主动重置时用） */
export function forgetListUrl(key: string): void {
  try {
    sessionStorage.removeItem(PREFIX + key);
  } catch {
    // 同上
  }
}
