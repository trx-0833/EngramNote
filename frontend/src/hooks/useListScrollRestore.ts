/**
 * @file 列表页的滚动位置恢复（2026-11-10）
 *
 * ## 解决什么
 *
 * 「从列表点进详情 → 返回」之后，列表滚回顶部，用户要重新往下找刚才那一行。
 * 这个 Hook 把 `window.scrollY` 按**一个键**（通常是"路由 + 当前筛选"）记住，
 * 只在下一次进入是**后退/前进**（`navigationType === 'POP'`）时恢复。
 *
 * ## 三个刻意的取舍
 *
 * | 决策 | 理由 |
 * |---|---|
 * | 只在 `POP` 时恢复 | 从侧边栏**点进**列表是一次新的开始（`PUSH`），跳到半路会让人莫名其妙。后退才意味着"我刚从这儿离开" |
 * | 键里带筛选条件 | 换了筛选就是另一份列表，行号与高度都对不上；带着旧 Y 去恢复会停在**别的位置** |
 * | `sessionStorage` 而不是 `localStorage` | 关掉标签页就该忘掉（与阅读位置记忆 `utils/scrollMemory` 的长留存不同：那是"接着上次读"，这是"这次浏览的连续性"） |
 *
 * ## 为什么要"等高度够了再滚"
 *
 * 列表数据来自 react-query 缓存，通常**同步**就有；但首次进入（缓存冷）时
 * 列表是空的，文档只有几百像素高，这时 `scrollTo(3000)` 会被浏览器钳到当前
 * 最大高度，等数据渲染完用户停在半路。与 `useScrollMemory` 同一套做法：
 * 有界重试（上限 1.5 秒），够高就滚、超时保持顶部 —— 不无限等一个可能不来的高度。
 */
import { useEffect, useRef } from 'react';

/** 恢复位置的重试上限；超过就放弃（保持当前位置），见文件头 */
const RESTORE_TIMEOUT_MS = 1500;

/** 滚动写盘的节流间隔（拖动滚动条时不要每帧写 sessionStorage） */
const WRITE_THROTTLE_MS = 150;

/** 小于这个值视为"就在顶部"，不为几像素制造一次跳动 */
const RESTORE_MIN_Y = 8;

const STORAGE_PREFIX = 'engramnote:listScroll:';

/**
 * 上一次导航是不是"后退/前进"
 *
 * ## 为什么不用 `useNavigationType()`
 *
 * 那个 Hook 要求组件在 Router 上下文里。列表页的**单元测试**不套路由
 * （`renderWithProviders(<Trash />)` 这种），一调就抛
 * `Cannot read properties of null (reading 'navigationType')` —— 为了一个
 * 滚动恢复去改一堆测试的渲染方式，是把测试绑死在实现上。
 *
 * `popstate` 只由后退/前进触发（`pushState` 不发），所以模块级监听一次就够：
 * 事件先于新组件挂载到达，挂载时读标记、读后即清。
 */
let lastNavigationWasPop = false;

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => {
    lastNavigationWasPop = true;
  });
}

function consumePopFlag(): boolean {
  const value = lastNavigationWasPop;
  lastNavigationWasPop = false;
  return value;
}

function storageKey(key: string): string {
  return `${STORAGE_PREFIX}${key}`;
}

function readSavedY(key: string): number | null {
  try {
    const raw = sessionStorage.getItem(storageKey(key));
    if (!raw) return null;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  } catch {
    // 隐私模式 / 存储被禁用：当作没有记忆，不影响页面
    return null;
  }
}

function writeSavedY(key: string, y: number): void {
  try {
    sessionStorage.setItem(storageKey(key), String(Math.max(0, Math.round(y))));
  } catch {
    // 同上：写不进去就算了
  }
}

/**
 * 记住并恢复当前列表的滚动位置
 *
 * @param key - 记忆键。**必须把筛选条件编进去**（例如
 *   `notes:material:archived=0:q=电池:p=2`），否则换筛选后行号与高度错位
 */
export function useListScrollRestore(key: string): void {
  const keyRef = useRef(key);
  /** 这次挂载是不是"后退/前进"进来的（只在首次渲染时消费一次标记） */
  const isPopEntry = useRef<boolean | null>(null);
  if (isPopEntry.current === null) {
    isPopEntry.current = consumePopFlag();
  }

  // 键变化（筛选/翻页）：先把旧键的位置存下来，再切换
  useEffect(() => {
    if (keyRef.current !== key) {
      writeSavedY(keyRef.current, window.scrollY);
      keyRef.current = key;
    }
  }, [key]);

  // 持续记录（节流）+ 卸载时补一次
  useEffect(() => {
    let lastWrite = 0;
    const onScroll = () => {
      const now = Date.now();
      if (now - lastWrite < WRITE_THROTTLE_MS) return;
      lastWrite = now;
      writeSavedY(key, window.scrollY);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      writeSavedY(key, window.scrollY);
    };
  }, [key]);

  // 恢复：只在"后退/前进"时做
  useEffect(() => {
    if (!isPopEntry.current) return;
    const target = readSavedY(key);
    if (target === null || target < RESTORE_MIN_Y) return;

    let cancelled = false;
    const deadline = Date.now() + RESTORE_TIMEOUT_MS;
    let retryTimer: number | undefined;

    const tryRestore = () => {
      if (cancelled) return;
      const maxY = document.documentElement.scrollHeight - window.innerHeight;
      if (maxY >= target) {
        window.scrollTo({ top: target, behavior: 'auto' });
        return;
      }
      if (Date.now() >= deadline) {
        // 高度一直不够（列表被筛空 / 数据没回来）：保持顶部，别把人丢在半空
        return;
      }
      retryTimer = window.setTimeout(tryRestore, 50);
    };

    // 先让这一帧渲染完（列表可能刚挂载）
    retryTimer = window.setTimeout(tryRestore, 0);

    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
    // 只按 key 变化触发：isPopEntry 是进入时算好的快照，后续不该重跑
  }, [key]);
}
