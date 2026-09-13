/**
 * @file 图谱页的契约漂移归一化
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 *
 * 这一组函数是 9 条白屏路径的护栏（overhaul-plan 附录 AZ / AZ.6）：接口少一个数组字段、
 * 或返回 `{items:[…]}` 包装对象时，**在入口处**归一化，下游一律读归一化后的值，
 * 而不是在每个使用点补 `?.`。拆分时逐字搬运 —— 判定条件（`Array.isArray`
 * 而不是 `?? []`）、返回结构与调用时机均未改动，改错一处就是白屏。
 */
import type { GraphData, GraphStats, NodeSubgraph, SuggestedRelation } from '../../api/client'

/**
 * 契约漂移兜底：接口响应少了 `nodes` / `edges`（或给的不是数组）时**在入口处**归一成空数组。
 * `nodes` 缺失曾经直接崩在 `graphData.nodes.filter`（渲染期异常 → 整页被错误边界接走），
 * 与 NoteDetail 的 `noteLinks` 是同一类缺陷。缺数组应当退化成"暂无图谱数据"，
 * 而不是白屏；下游一律读归一化后的值，无需每个使用点再补 `?.`。
 * 用 `Array.isArray` 而不是 `?? []`：字段在但类型不对（如 `{nodes: {}}`）同样要兜住。
 */
export function normalizeGraphData(data: GraphData | null | undefined): GraphData | null {
  if (!data) return null
  return {
    ...data,
    nodes: Array.isArray(data.nodes) ? data.nodes : [],
    edges: Array.isArray(data.edges) ? data.edges : [],
  }
}

/** `/graph/suggestions` 契约漂移：后端若回 `{items:[...]}` 包装而不是纯数组，取内层数组 */
export function normalizeSuggestions(data: unknown): SuggestedRelation[] {
  if (Array.isArray(data)) return data as SuggestedRelation[]
  const items = (data as { items?: unknown } | null | undefined)?.items
  return Array.isArray(items) ? (items as SuggestedRelation[]) : []
}

/** `relation_type_distribution` 缺失/类型不对时退化成空分布：统计面板只少一段条形图，不整页崩 */
export function normalizeStats(data: GraphStats | null | undefined): GraphStats | null {
  if (!data) return null
  return {
    ...data,
    relation_type_distribution: Array.isArray(data.relation_type_distribution)
      ? data.relation_type_distribution
      : [],
  }
}

/** 子图响应契约漂移：缺 `center_node` 视为无子图（面板不出现），缺邻居数组退化成空列表 */
export function normalizeSubgraph(data: NodeSubgraph | null | undefined): NodeSubgraph | null {
  if (!data || !data.center_node) return null
  return {
    ...data,
    neighbor_nodes: Array.isArray(data.neighbor_nodes) ? data.neighbor_nodes : [],
    edges: Array.isArray(data.edges) ? data.edges : [],
  }
}
