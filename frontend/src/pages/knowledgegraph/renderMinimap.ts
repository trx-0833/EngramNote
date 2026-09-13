/**
 * @file 图谱缩略图（minimap）绘制
 * @description 自 `pages/KnowledgeGraph.tsx` 的 `drawMinimap` 拆分（overhaul-plan 5.5），
 * **只搬不改**：画布尺寸、宣纸底色、边/节点的归一化坐标换算、视口框的虚线绘制
 * 全部逐字保留，仅把闭包读取的 ref（canvas / graphData / viewport / 容器）
 * 改成显式入参。
 *
 * `getContext('2d')` 仍在本函数内、且在**数据为空时不会被调用**（与拆分前同一顺序）：
 * jsdom 里 `getContext` 返回 null，页面据此提前返回，不画任何东西。
 */
import type { GraphData } from '../../api/client'
import type { ForceGraphNode } from '../../components/graph/types'
import { cardTypeColors as CARD_TYPE_COLORS } from '../../utils/labels'

/** 当前视口信息（由 GraphCanvas 的 onZoom 写入） */
export interface GraphViewport {
  k: number
  x: number
  y: number
}

/**
 * 绘制 minimap 缩略图
 *
 * @param canvas 缩略图画布（未挂载时为 null，直接返回）
 * @param graphData 归一化后的图谱数据（为空时不绘制）
 * @param viewport 当前视口（k / x / y）
 * @param graphContainer 画布容器（用于换算视口框尺寸，未挂载时跳过视口框）
 */
export function renderMinimap(
  canvas: HTMLCanvasElement | null,
  graphData: GraphData | null,
  viewport: GraphViewport,
  graphContainer: HTMLElement | null,
) {
  if (!canvas || !graphData || graphData.nodes.length === 0) return

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const W = canvas.width
  const H = canvas.height

  const nodes = graphData.nodes as ForceGraphNode[]
  const xs = nodes.map((n) => n.x || 0)
  const ys = nodes.map((n) => n.y || 0)
  let minX = Math.min(...xs), maxX = Math.max(...xs)
  let minY = Math.min(...ys), maxY = Math.max(...ys)
  if (maxX - minX < 1) { maxX = minX + 1 }
  if (maxY - minY < 1) { maxY = minY + 1 }

  const padX = (maxX - minX) * 0.1
  const padY = (maxY - minY) * 0.1
  minX -= padX; maxX += padX
  minY -= padY; maxY += padY

  const scaleX = W / (maxX - minX)
  const scaleY = H / (maxY - minY)
  const scale = Math.min(scaleX, scaleY)
  const offsetX = (W - (maxX - minX) * scale) / 2
  const offsetY = (H - (maxY - minY) * scale) / 2

  const mx = (x: number) => (x - minX) * scale + offsetX
  const my = (y: number) => (y - minY) * scale + offsetY

  ctx.clearRect(0, 0, W, H)
  ctx.fillStyle = 'rgba(245, 240, 228, 0.75)' // 宣纸底（原: rgba(250, 249, 247, 0.6)）
  ctx.fillRect(0, 0, W, H)

  ctx.strokeStyle = 'rgba(154, 154, 176, 0.4)'
  ctx.lineWidth = 0.5
  graphData.edges.forEach((e) => {
    const src = nodes.find((n) => n.id === e.source)
    const tgt = nodes.find((n) => n.id === e.target)
    if (!src || !tgt) return
    ctx.beginPath()
    ctx.moveTo(mx(src.x || 0), my(src.y || 0))
    ctx.lineTo(mx(tgt.x || 0), my(tgt.y || 0))
    ctx.stroke()
  })

  nodes.forEach((n) => {
    const color = CARD_TYPE_COLORS[n.card_type] || '#6b7280'
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(mx(n.x || 0), my(n.y || 0), 1.5, 0, 2 * Math.PI)
    ctx.fill()
  })

  // 绘制当前视口框
  const vp = viewport
  if (graphContainer) {
    const vw = graphContainer.clientWidth / vp.k
    const vh = graphContainer.clientHeight / vp.k
    const vx = -vp.x / vp.k
    const vy = -vp.y / vp.k
    ctx.strokeStyle = 'rgba(15, 52, 96, 0.7)'
    ctx.lineWidth = 1
    ctx.setLineDash([2, 2])
    ctx.strokeRect(mx(vx), my(vy), vw * scale, vh * scale)
    ctx.setLineDash([])
  }
}
