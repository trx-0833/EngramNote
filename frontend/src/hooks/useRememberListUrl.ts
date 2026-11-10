/**
 * @file 列表卸载时记住自己的完整 URL（2026-11-10）
 *
 * 与 `useBackToList` 配对：详情页返回时若没有历史深度可用，就回到这里记住的
 * URL（含 `?q=&role=&p=`），而不是一个裸路径。存储层与理由见
 * `utils/listReturnMemory.ts`。
 *
 * ## 为什么在"卸载"时记，而不是每次筛选变化时记
 *
 * 筛选变化时写一次意味着**每次按键**都写 sessionStorage（同为 200ms 节流的
 * 滚动记忆都要节流，何况这个）。卸载时写一次就够 —— 列表卸载 = 用户正要离开，
 * 这一刻的 URL 才是"他离开时看到的那一屏"。
 *
 * @param key - 记忆键，与 `useBackToList(fallback)` 的 fallback 去掉斜杠后一致
 *   （例如列表在 `/notes`，key 就是 `notes`）
 */
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

import { rememberListUrl } from '../utils/listReturnMemory';

export function useRememberListUrl(key: string): void {
  const location = useLocation();
  const url = `${location.pathname}${location.search}`;

  /** 最新 URL 的镜像：卸载回调里要读到"最后一刻"的值，而不是首次渲染的值 */
  const urlRef = useRef(url);

  useEffect(() => {
    // 在 effect 里镜像（而不是渲染期间赋值 —— 那会触发
    // `react-hooks/refs` 的 "Cannot access refs during render"）
    urlRef.current = url;
  }, [url]);

  useEffect(() => {
    // 依赖只有 key：URL 变化**不**重跑清理（否则每次按键都会写一次 sessionStorage）
    return () => {
      rememberListUrl(key, urlRef.current);
    };
  }, [key]);
}
