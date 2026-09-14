/**
 * @file 知识图谱可视化组件共享类型与常量
 * @description 自 pages/KnowledgeGraph.tsx 拆分（只搬不改），供 components/graph/* 复用。
 */
import type { ForceGraphMethods, NodeObject, LinkObject } from 'react-force-graph-2d'
import type { GraphNode } from '../../api/client'

/** 关系类型 → 中文标签 */
export const RELATION_TYPE_LABELS: Record<string, string> = {
  related: '相关',
  prerequisite: '前置',
  subsequent: '后续',
  contrast: '对比',
}

/** 关系类型 → 边颜色（学术主题色板） */
export const RELATION_TYPE_COLORS: Record<string, string> = {
  related: '#9a9ab0',       // 暖灰
  prerequisite: '#0f3460',  // 深海蓝
  subsequent: '#2d8a56',    // 墨绿
  contrast: '#c0392b',      // 朱红
}

/** 节点形状类型 */
export type NodeShape = 'circle' | 'diamond' | 'rounded' | 'hexagon'

/** 卡片类型 → 节点形状 */
export const CARD_TYPE_SHAPES: Record<string, NodeShape> = {
  concept: 'circle',
  formula: 'diamond',
  qa: 'rounded',
  definition: 'hexagon',
}

/** 卡片类型 → 节点内显示的首字 */
export const CARD_TYPE_INITIALS: Record<string, string> = {
  concept: '概',
  formula: '式',
  qa: '问',
  definition: '定',
}

/** 关系类型选项，用于创建关系表单 */
export const RELATION_TYPE_OPTIONS = [
  { value: 'related', label: '相关' },
  { value: 'prerequisite', label: '前置' },
  { value: 'subsequent', label: '后续' },
  { value: 'contrast', label: '对比' },
]

/** 力导向图内部节点类型 */
export interface ForceGraphNode extends GraphNode {
  x?: number
  y?: number
  __bckgDimensions?: [number, number]
}

/** 力导向图内部边类型 */
export interface ForceGraphLink {
  id: string
  source: string | ForceGraphNode
  target: string | ForceGraphNode
  relation_type: string
  status: string
  /**
   * 相似度分数
   *
   * 阶段 5.1 / S2：契约里 `GraphEdge.similarity_score` 是 `?: number | null`
   * （既可能缺省、也可能是 null；`GET /graph` 与 `/graph/node/{id}/subgraph`
   * 两条构建路径的口径本来就不同）。这里跟着改成可选 ——
   * 读取方（`getLinkWidth` / GraphCanvas / GraphSidebar）用的都是 `== null` / `!= null`，
   * 对 `undefined` 与 `null` 的处理**完全一致**，所以这是纯类型修正，
   * 没有任何运行时行为变化。
   */
  similarity_score?: number | null
}

/** 侧边栏面板类型 */
export type SidebarPanel = 'suggestions' | 'nodeDetail' | 'createRelation' | 'viewSubgraph' | null

/** 力导向图 ref 类型 */
export type GraphForceRef = ForceGraphMethods<NodeObject<ForceGraphNode>, LinkObject<ForceGraphNode, ForceGraphLink>>

/** 力导向图渲染数据 */
export interface ForceGraphViewData {
  nodes: ForceGraphNode[]
  links: ForceGraphLink[]
}

/**
 * 绘制不同形状的节点路径
 */
export function drawNodeShapePath(ctx: CanvasRenderingContext2D, shape: NodeShape, x: number, y: number, size: number) {
  ctx.beginPath()
  switch (shape) {
    case 'circle':
      ctx.arc(x, y, size, 0, 2 * Math.PI)
      break
    case 'diamond': {
      const d = size * 1.15
      ctx.moveTo(x, y - d)
      ctx.lineTo(x + d, y)
      ctx.lineTo(x, y + d)
      ctx.lineTo(x - d, y)
      ctx.closePath()
      break
    }
    case 'rounded': {
      const s = size * 0.95
      const r = size * 0.25
      ctx.moveTo(x - s + r, y - s)
      ctx.lineTo(x + s - r, y - s)
      ctx.quadraticCurveTo(x + s, y - s, x + s, y - s + r)
      ctx.lineTo(x + s, y + s - r)
      ctx.quadraticCurveTo(x + s, y + s, x + s - r, y + s)
      ctx.lineTo(x - s + r, y + s)
      ctx.quadraticCurveTo(x - s, y + s, x - s, y + s - r)
      ctx.lineTo(x - s, y - s + r)
      ctx.quadraticCurveTo(x - s, y - s, x - s + r, y - s)
      ctx.closePath()
      break
    }
    case 'hexagon': {
      for (let i = 0; i < 6; i++) {
        const angle = (Math.PI / 3) * i - Math.PI / 2
        const px = x + size * Math.cos(angle)
        const py = y + size * Math.sin(angle)
        if (i === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      }
      ctx.closePath()
      break
    }
  }
}

/** 计算节点大小 */
export function getNodeSize(node: ForceGraphNode): number {
  return Math.min(15, Math.max(5, 5 + (node.relation_count || 0) * 2))
}

/** 计算边宽度 */
export function getLinkWidth(link: ForceGraphLink): number {
  const score = link.similarity_score
  if (score == null) return 1.5
  return Math.min(3, Math.max(1, score * 3))
}