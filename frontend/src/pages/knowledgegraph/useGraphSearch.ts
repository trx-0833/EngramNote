/**
 * @file 图谱页的节点搜索：输入状态 + 300ms 防抖 + 搜索请求
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 防抖间隔 300ms、关键词清空时同步清空结果、请求失败退化成空结果列表
 * （接口返回残缺结构时不崩、也不残留旧结果）均与拆分前逐字一致。
 *
 * 唯一补充：结果数组原来写的是 `result.items || []`（本目录最后一处没走判据的列表赋值），
 * 现在过 `unwrapPageItems` —— 与 `/notes` 同一个形状（`{items,total}`，`items` 就是约定字段），
 * 因此漂移会被报出来，而不是静默容忍。退化行为不变：非数组 → 空列表，
 * 消费侧（`GraphToolbar` 的 `searchResults.length > 0`）照旧兜底。
 */
import { useEffect, useRef, useState } from 'react';
import { searchGraphNodes, type GraphSearchNode } from '../../api/client';
import { unwrapPageItems } from '../contractDrift';

/** 提示文案里的接口名：与后端对账时直接用 */
const SOURCE_GRAPH_SEARCH = 'GET /graph/search';

export function useGraphSearch() {
  const [searchKeyword, setSearchKeyword] = useState('');
  const [searchResults, setSearchResults] = useState<GraphSearchNode[]>([]);
  const [searching, setSearching] = useState(false);
  const searchTimerRef = useRef<number | null>(null);

  // handleSearch 声明在 effect 之前：函数声明本身会被提升、**运行时与拆分前完全一致**
  // （拆分前它写在 effect 之后），但放在前面可避免 react-hooks 的
  // "Cannot access variable before it is declared" 诊断（v7 对拆分后的 hook 文件生效）。
  async function handleSearch(keyword: string) {
    setSearching(true);
    try {
      const result = await searchGraphNodes(keyword, 15);
      setSearchResults(unwrapPageItems<GraphSearchNode>(result, SOURCE_GRAPH_SEARCH));
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }

  // 搜索防抖（关键词清空时同步清空结果列表，派生状态重置豁免）
  useEffect(() => {
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
    }
    if (!searchKeyword.trim()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSearchResults([]);
      return;
    }
    searchTimerRef.current = window.setTimeout(() => {
      handleSearch(searchKeyword.trim());
    }, 300);
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [searchKeyword]);

  return {
    searchKeyword,
    setSearchKeyword,
    searchResults,
    searching,
  };
}
