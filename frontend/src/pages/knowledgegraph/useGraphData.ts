/**
 * @file 图谱页的数据层：图谱 / 建议 / 统计的加载与归一化
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 三个接口用 `Promise.all` 并发、失败时把 `Error.message` 原样作为 `error`（"重试"按钮据此重跑）、
 * 以及三处归一化（`normalizeGraphData` / `normalizeSuggestions` / `normalizeStats`）
 * 的位置与时序均与拆分前一致。三处归一化的**判据**在 `pages/contractDrift.ts`：
 * 容忍度一字未变，只是形状漂移从此会被报出来（AZ.7），由本 hook 接到全局 toast。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  getGraphData,
  getSuggestions,
  getGraphStats,
  type GraphData,
  type SuggestedRelation,
  type GraphStats,
} from '../../api/client'
import { normalizeGraphData, normalizeStats, normalizeSuggestions } from './normalize'
import { useContractDriftNotice } from '../contractDrift'

/** 图谱页数据层：图谱本体、建议列表、统计数据与加载/失败状态 */
export function useGraphData() {
  const [rawGraphData, setGraphData] = useState<GraphData | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestedRelation[]>([])
  const [stats, setStats] = useState<GraphStats | null>(null)
  /** 归一化后的图谱数据：`nodes` / `edges` 保证是数组（见 normalizeGraphData） */
  const graphData = useMemo(() => normalizeGraphData(rawGraphData), [rawGraphData])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 契约漂移的提示出口：本页所有归一化（图谱/建议/统计，含写操作后的重拉）都从这里接到 toast。
  // 只在本 hook 里挂一次就够 —— 上报是全局的，不由订阅方决定。
  useContractDriftNotice()

  useEffect(() => {
    fetchData()
  }, [])

  async function fetchData() {
    setLoading(true)
    setError('')
    try {
      const [data, sugData, statsData] = await Promise.all([
        getGraphData(),
        getSuggestions(),
        getGraphStats(),
      ])
      setGraphData(data)
      // 后端 /graph/suggestions 返回纯数组，直接赋值；包装对象/缺失时归一成空列表
      setSuggestions(normalizeSuggestions(sugData))
      setStats(normalizeStats(statsData))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载图谱失败')
    } finally {
      setLoading(false)
    }
  }

  return {
    /** 归一化后的图谱数据（渲染与画布都用它，不再直接用 rawGraphData） */
    graphData,
    suggestions,
    stats,
    loading,
    error,
    fetchData,
    /** 关系确认/拒绝/创建/删除后重新拉取，写回原始响应（再由 memo 归一化） */
    setGraphData,
    setSuggestions,
    setStats,
  }
}
