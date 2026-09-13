/**
 * @file 图谱页的节点搜索：输入状态 + 300ms 防抖 + 搜索请求
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 防抖间隔 300ms、关键词清空时同步清空结果、请求失败退化成空结果列表
 * （接口返回残缺结构时不崩、也不残留旧结果）均与拆分前逐字一致。
 */
import { useEffect, useRef, useState } from 'react'
import { searchGraphNodes, type GraphSearchNode } from '../../api/client'

export function useGraphSearch() {
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<GraphSearchNode[]>([])
  const [searching, setSearching] = useState(false)
  const searchTimerRef = useRef<number | null>(null)

  // handleSearch 声明在 effect 之前：函数声明本身会被提升、**运行时与拆分前完全一致**
  // （拆分前它写在 effect 之后），但放在前面可避免 react-hooks 的
  // "Cannot access variable before it is declared" 诊断（v7 对拆分后的 hook 文件生效）。
  async function handleSearch(keyword: string) {
    setSearching(true)
    try {
      const result = await searchGraphNodes(keyword, 15)
      setSearchResults(result.items || [])
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  // 搜索防抖（关键词清空时同步清空结果列表，派生状态重置豁免）
  useEffect(() => {
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current)
    }
    if (!searchKeyword.trim()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSearchResults([])
      return
    }
    searchTimerRef.current = window.setTimeout(() => {
      handleSearch(searchKeyword.trim())
    }, 300)
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    }
  }, [searchKeyword])

  return {
    searchKeyword,
    setSearchKeyword,
    searchResults,
    searching,
  }
}
