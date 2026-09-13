/**
 * @file 建议关系的勾选状态（批量确认/拒绝的选择集）
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 全选/部分选中的两个派生判定（`length > 0` 与 `size === length` 的组合）、
 * 以及"部分选中时把全选框的 `indeterminate` 置真"的 effect 全部逐字保留。
 *
 * 这两条判定是测试断言过的：一条建议都不勾时 `batchConfirm` 必须禁用，
 * 勾了一部分时全选框必须是横杠而不是"已全选"。
 */
import { useEffect, useRef, useState } from 'react'
import type { SuggestedRelation } from '../../api/client'

export function useSuggestionSelection(suggestions: SuggestedRelation[]) {
  /** 批量选择 */
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set())
  /** 全选框引用，用于展示「部分选中」的 indeterminate 状态 */
  const selectAllRef = useRef<HTMLInputElement>(null)

  /** 切换选择 */
  function toggleSuggestion(id: string) {
    setSelectedSuggestions((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  /** 当前建议是否已全部选中 */
  const allSuggestionsSelected =
    suggestions.length > 0 && selectedSuggestions.size === suggestions.length
  /** 当前建议是否部分选中（用于全选框 indeterminate 状态） */
  const someSuggestionsSelected =
    selectedSuggestions.size > 0 && !allSuggestionsSelected

  /** 全选 / 全不选：已全选则清空，否则选中全部 */
  function toggleSelectAll() {
    if (allSuggestionsSelected) {
      setSelectedSuggestions(new Set())
    } else {
      setSelectedSuggestions(new Set(suggestions.map((s) => s.id)))
    }
  }

  /** 全选框部分选中时展示 indeterminate（横杠）状态 */
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someSuggestionsSelected
    }
  }, [someSuggestionsSelected])

  return {
    selectedSuggestions,
    clearSelection: () => setSelectedSuggestions(new Set()),
    selectAllRef,
    toggleSuggestion,
    allSuggestionsSelected,
    someSuggestionsSelected,
    toggleSelectAll,
  }
}
