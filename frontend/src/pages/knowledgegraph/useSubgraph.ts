/**
 * @file 图谱页的子图查看：加载中心节点及其邻居
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 先置 loading、再打开 `viewSubgraph` 面板并展开侧边栏、失败时把子图数据清空
 * （不残留上一次的子图）—— 三步顺序与拆分前一致；响应统一过 `normalizeSubgraph`
 * （缺 `center_node` → 面板不出现；缺 `neighbor_nodes` → "0 个关联节点"）。
 */
import { useState, type Dispatch, type SetStateAction } from 'react'
import { getNodeSubgraph, type NodeSubgraph } from '../../api/client'
import type { SidebarPanel } from '../../components/graph/types'
import { normalizeSubgraph } from './normalize'

interface UseSubgraphOptions {
  setActivePanel: Dispatch<SetStateAction<SidebarPanel>>
  setSidebarOpen: Dispatch<SetStateAction<boolean>>
}

export function useSubgraph({ setActivePanel, setSidebarOpen }: UseSubgraphOptions) {
  const [subgraphData, setSubgraphData] = useState<NodeSubgraph | null>(null)
  const [loadingSubgraph, setLoadingSubgraph] = useState(false)

  /** 加载节点子图 */
  async function loadSubgraph(nodeId: string) {
    setLoadingSubgraph(true)
    setActivePanel('viewSubgraph')
    setSidebarOpen(true)
    try {
      const data = await getNodeSubgraph(nodeId)
      setSubgraphData(normalizeSubgraph(data))
    } catch {
      setSubgraphData(null)
    } finally {
      setLoadingSubgraph(false)
    }
  }

  return { subgraphData, setSubgraphData, loadingSubgraph, loadSubgraph }
}
