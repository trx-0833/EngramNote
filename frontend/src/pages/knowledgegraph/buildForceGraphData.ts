/**
 * @file 图谱页的画布数据装配（回收站过滤 + 类型过滤）
 * @description 自 `pages/KnowledgeGraph.tsx` 的 `forceGraphData` useMemo 拆分
 * （overhaul-plan 5.5），**只搬不改**：过滤顺序、`visibleIds` 的构造、
 * 类型过滤下"保留被过滤类型节点 + 与之关联的节点"的语义、以及每项 `{...n}` / `{...e}`
 * 的浅拷贝（避免 force-graph 把坐标写回接口数据）均与拆分前逐字一致。
 */
import type { GraphData } from '../../api/client';
import type { ForceGraphViewData } from '../../components/graph/types';

/** 将 GraphData 转为 ForceGraph2D 所需格式 */
export function buildForceGraphData(
  graphData: GraphData | null,
  filterCardType: string | null,
): ForceGraphViewData {
  if (!graphData) return { nodes: [], links: [] };

  // 本函数在组件早退之前执行：即便 graphData 缺 nodes/edges（后端只回一半、
  // 或命中旧结构缓存）也必须自身安全，所以两处都归一成数组再用。
  let nodes = graphData.nodes ?? [];
  let edges = graphData.edges ?? [];

  // 回收站过滤：所属笔记已进回收站的节点不渲染（关系记录后端保留，
  // 恢复后自动复原）；同时剔除指向回收站节点的边，避免 force-graph 生成幽灵节点
  const visibleIds = new Set(nodes.filter((n) => !n.note_trashed).map((n) => n.id));
  nodes = nodes.filter((n) => visibleIds.has(n.id));
  edges = edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target));

  // 类型过滤
  if (filterCardType) {
    const filteredNodeIds = new Set(
      nodes.filter((n) => n.card_type === filterCardType).map((n) => n.id),
    );
    const connectedNodeIds = new Set<string>();
    edges.forEach((e) => {
      if (filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target)) {
        connectedNodeIds.add(e.source);
        connectedNodeIds.add(e.target);
      }
    });
    // 显示被过滤类型节点 + 与之关联的节点（保持图的结构完整）
    nodes = nodes.filter((n) => connectedNodeIds.has(n.id));
    edges = edges.filter((e) => filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target));
  }

  return {
    nodes: nodes.map((n) => ({ ...n })),
    links: edges.map((e) => ({
      ...e,
      source: e.source,
      target: e.target,
    })),
  };
}
