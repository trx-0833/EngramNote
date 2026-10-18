/**
 * @file 阅读位置记忆 Hook：把「上次读到哪」接到真实的滚动上
 * @description 「使用时的想法.txt」第 1 条的接线层，存储层见 `utils/scrollMemory.ts`
 *
 * ## 这个页面由**窗口**滚动
 *
 * `App.module.css` 的 `.main` 只有居中 + 内边距，`html` / `body` / `.main`
 * 上都没有 `overflow-y`（实测 grep 全 `src` 无命中）—— 所以位置就是
 * `window.scrollY`，不是某个内层容器的 `scrollTop`。
 * `DailyMaterials.tsx:119-121` 对同一件事有过同样的判断，此处沿用。
 *
 * ## 真正的难点：正文是**异步**撑开文档高度的
 *
 * `getNote()` 回来后正文才渲染（`pages/notedetail/useNoteDetailData.ts:122-127`），
 * 在此之前文档只有几百像素高。这时 `window.scrollTo({top: 3000})` 会被浏览器
 * **钳到当前最大高度** —— 然后正文渲染完，用户停在半路。
 * 「滚了一半」比「回到顶部」更让人困惑，所以这里必须等高度够了再滚。
 *
 * 用的是**有界重试**而不是 `ResizeObserver` 回调里滚一次：
 * 正文是 `dangerouslySetInnerHTML` 一次性写入的，高度可能在一次布局里就到位，
 * 也可能分几帧（图片、KaTeX、字体）。观察者负责"高度变了再看一眼"，
 * 帧循环负责"再看一眼" —— 两条腿都短，但合起来能覆盖到。
 * 上限 1.5 秒：超过就**放弃并保持顶部**，不无限等一个可能永远不来的高度。
 *
 * ## 三个「不恢复」的分支，各有理由
 *
 * | 分支 | 为什么 |
 * |---|---|
 * | `skipRestore`（引用回跳） | `utils/citationJump.ts:151` 已经在滚向被引用的段落。两个滚动打架时，用户点的那一下必须赢 |
 * | 用户自己先滚了 | 恢复的意义是"接着上次看"，用户主动滚走说明他不想接着上次看 |
 * | 视图模式不一致 | 原始版与清洗版的段落位置完全不同，跨模式恢复会落到**错误的地方**。宁可回顶部 |
 */
import { useEffect, useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';
import { readScrollMemory, writeScrollMemory } from '../utils/scrollMemory';

/** 滚动停止多久之后才落盘。太短会让长文档拖动时每帧都写 localStorage */
const WRITE_THROTTLE_MS = 200;

/** 恢复位置的重试上限。超过就放弃（保持顶部），见文件头 */
const RESTORE_TIMEOUT_MS = 1500;

/**
 * 小于这个值视为"就在顶部"。
 *
 * 用途只有一个：**不为了几像素去滚**。刷新后浏览器本来就在 0，
 * 为 `y = 3` 调一次 `scrollTo` 只会制造一次无意义的跳动。
 */
const RESTORE_MIN_Y = 8;

interface UseScrollMemoryOptions {
  /** 当前笔记 ID。换笔记时重新读记忆 */
  noteId: string | undefined;
  /** 正文容器。它**存在**是"正文已渲染"的判据（见文件头） */
  containerRef: RefObject<HTMLElement>;
  /** 是否处于"可以被记住"的阅读状态（编辑态 / diff 态不该记） */
  enabled: boolean;
  /** 当前视图模式，随记忆一起存，恢复时要求一致 */
  mode: string;
  /** 本次进入是引用回跳：**不**恢复位置，把滚动让给 `citationJump` */
  skipRestore: boolean;
}

/**
 * 记住并恢复某篇笔记的阅读位置。
 *
 * @returns 无返回值 —— 页面不需要知道它做了什么，也不该有"恢复中"的中间态
 */
export function useScrollMemory({
  noteId,
  containerRef,
  enabled,
  mode,
  skipRestore,
}: UseScrollMemoryOptions): void {
  /** 挂载时读到的待恢复位置（`null` = 这次不恢复） */
  const pendingRef = useRef<number | null>(null);
  /** 恢复是否已经发生（成功或放弃）。`false` 时重试循环继续跑 */
  const doneRef = useRef(true);
  /** 用户是否已经自己滚过（滚过就放弃恢复） */
  const userScrolledRef = useRef(false);

  /** 供事件处理器读取的最新值 —— 它们挂在 window 上，闭包会过期 */
  const enabledRef = useRef(enabled);
  const modeRef = useRef(mode);
  const noteIdRef = useRef(noteId);
  // latest-ref 模式：事件处理器与清理函数必须在渲染期同步最新值，
  // 推迟到 effect 会让首次交互读到旧闭包（与 `useAdhdReader.ts:168-171` 同款，
  // 官方规则的 StrictMode 例外场景，故豁免 react-hooks/refs）
  // eslint-disable-next-line react-hooks/refs
  enabledRef.current = enabled;
  // eslint-disable-next-line react-hooks/refs
  modeRef.current = mode;
  // eslint-disable-next-line react-hooks/refs
  noteIdRef.current = noteId;

  // ── 读记忆 + 决定这次要不要恢复 ──
  // 用 `useLayoutEffect`：要在**浏览器绘制之前**把"待恢复"这件事定下来，
  // 否则用户会先看到顶部的页面再看到它跳走（`Dialog.tsx:192-203` 同样的理由）。
  useLayoutEffect(() => {
    userScrolledRef.current = false;

    if (!noteId || !enabled || skipRestore) {
      pendingRef.current = null;
      doneRef.current = true;
      return;
    }

    const memory = readScrollMemory(noteId);
    // 模式不一致：位置没有可比性，宁可回顶部（见文件头）
    if (!memory || memory.mode !== mode || memory.y < RESTORE_MIN_Y) {
      pendingRef.current = null;
      doneRef.current = true;
      return;
    }

    pendingRef.current = memory.y;
    doneRef.current = false;
  }, [noteId, enabled, mode, skipRestore]);

  // ── 恢复：等正文撑开高度，有界重试 ──
  useEffect(() => {
    if (doneRef.current || pendingRef.current === null) return;

    const target = pendingRef.current;
    const startedAt = Date.now();

    /**
     * 排下一帧重试。
     *
     * 为什么不直接用 `window.requestAnimationFrame`：**jsdom 默认没有它**
     * （`vite.config.ts` 的 `test.environment` 是 `'jsdom'`，且没开
     * `pretendToBeVisual`）。本仓库为同类问题立过规矩 ——
     * `components/graph/GraphCanvas.tsx:129-136` 对 `ResizeObserver`
     * 写了「不判类型直接用会抛 `ReferenceError`，而它是在 effect 里抛的」，
     * 并按类型判断降级。
     *
     * 这里的降级不是"为迁就测试环境改产品代码"：`requestAnimationFrame`
     * 在真浏览器上一直有，判类型只是让**没有它的环境**退化成定时器重试，
     * 而重试本身是等价的（只是节拍粗一点）。
     */
    let frame = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = (fn: () => void) => {
      if (typeof window.requestAnimationFrame === 'function') {
        frame = window.requestAnimationFrame(fn);
      } else {
        timer = setTimeout(fn, 16);
      }
    };
    const unschedule = () => {
      if (typeof window.cancelAnimationFrame === 'function') window.cancelAnimationFrame(frame);
      if (timer !== null) clearTimeout(timer);
    };

    /** 高度够不够滚到目标位置。`scrollHeight` 不够时滚了也会被钳住 */
    const canReach = (): boolean => {
      const container = containerRef.current;
      if (!container) return false; // 正文还没渲染
      const maxY = document.documentElement.scrollHeight - window.innerHeight;
      return maxY >= target;
    };

    const finish = () => {
      doneRef.current = true;
      pendingRef.current = null;
    };

    const attempt = () => {
      // 用户自己滚了 / 已经切走 —— 放弃，不跟用户抢
      if (userScrolledRef.current || !enabledRef.current) {
        finish();
        return;
      }
      if (canReach()) {
        // `behavior: 'instant'`：这里要的是"已经在原处"，不是"看着它滚过去"。
        // 全局 `html { scroll-behavior: smooth }`（base.css:231）会让默认值变成平滑，
        // 那样刷新后用户会看到页面从顶部滑下去 —— 恢复应当是无感的。
        window.scrollTo({ top: target, behavior: 'instant' as ScrollBehavior });
        finish();
        return;
      }
      if (Date.now() - startedAt > RESTORE_TIMEOUT_MS) {
        // 超时：放弃，保持顶部。宁可不恢复，也不要停在一个错误的位置
        finish();
        return;
      }
      schedule(attempt);
    };

    // 高度变化的观察者：正文渲染完 / 图片与 KaTeX 到位都会触发
    const container = containerRef.current;
    const observer =
      typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => {
            if (!doneRef.current) attempt();
          })
        : null;
    if (observer && container) observer.observe(container);

    schedule(attempt);

    return () => {
      unschedule();
      observer?.disconnect();
    };
  }, [containerRef, noteId, enabled, mode, skipRestore]);

  // ── 保存：滚动节流写 + 页面隐藏时兜底写 ──
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;

    /** 把当前位置落盘。所有写入都走这里，保证 `enabled` / `noteId` 的判据只有一份 */
    const save = () => {
      if (!enabledRef.current) return;
      const id = noteIdRef.current;
      if (!id) return;
      writeScrollMemory(id, window.scrollY, modeRef.current);
    };

    const onScroll = () => {
      // 第一次滚动就说明用户接管了 —— 立刻放弃待恢复值
      if (!doneRef.current) userScrolledRef.current = true;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        save();
      }, WRITE_THROTTLE_MS);
    };

    /**
     * 页面被隐藏 / 卸载时兜底。
     *
     * 为什么不能只靠 `scroll` 的节流：节流窗口是 200ms，用户完全可能
     * "滚一下 → 立刻按 F5"，那 200ms 内的最后一次位置就丢了 ——
     * 而这正是本功能要覆盖的场景。`pagehide` 在刷新与关闭时都会触发。
     * 不用 `beforeunload`：它在移动端不可靠，且会阻止 bfcache。
     */
    const onPageHide = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
      save();
    };

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('pagehide', onPageHide);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('pagehide', onPageHide);
      if (timer !== null) clearTimeout(timer);
    };
  }, [noteId, enabled, mode]);
}
