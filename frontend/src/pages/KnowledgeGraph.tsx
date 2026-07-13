/**
 * @file 知识图谱可视化页面
 * @description 使用 react-force-graph-2d 渲染力导向图，展示知识卡片间的关联关系。
 * 支持节点交互、建议关系确认/拒绝、手动创建关系等功能。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ForceGraph2D from 'react-force-graph-2d'
import {
  getGraphData,
  getSuggestions,
  confirmRelation,
  rejectRelation,
  createRelation,
  deleteRelation,
  type GraphNode,
  type GraphData,
  type SuggestedRelation,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'

/** 卡片类型 → 节点颜色（学术主题色板） */
const CARD_TYPE_COLORS: Record<string, string> = {
  concept: '#2d8a56',      // 墨绿
  formula: '#0f3460',      // 深海蓝（主色）
  qa: '#c9a959',           // 墨金（辅助色）
  definition: '#6d28d9',   // 紫罗兰
}

/** 卡片类型 → 中文标签 */
const CARD_TYPE_LABELS: Record<string, string> = {
  concept: '概念',
  formula: '公式',
  qa: '问答',
  definition: '定义',
}

/** 关系类型 → 中文标签 */
const RELATION_TYPE_LABELS: Record<string, string> = {
  related: '相关',
  prerequisite: '前置',
  subsequent: '后续',
  contrast: '对比',
}

/** 关系类型 → 边颜色（学术主题色板） */
const RELATION_TYPE_COLORS: Record<string, string> = {
  related: '#9a9ab0',       // 暖灰
  prerequisite: '#0f3460',  // 深海蓝
  subsequent: '#2d8a56',    // 墨绿
  contrast: '#c0392b',      // 朱红
}

/** 节点形状类型 */
type NodeShape = 'circle' | 'diamond' | 'rounded' | 'hexagon'

/** 卡片类型 → 节点形状 */
const CARD_TYPE_SHAPES: Record<string, NodeShape> = {
  concept: 'circle',      // 概念：圆形
  formula: 'diamond',     // 公式：菱形
  qa: 'rounded',          // 问答：圆角方形
  definition: 'hexagon',  // 定义：六边形
}

/** 卡片类型 → 节点内显示的首字 */
const CARD_TYPE_INITIALS: Record<string, string> = {
  concept: '概',
  formula: '式',
  qa: '问',
  definition: '定',
}

/**
 * 绘制不同形状的节点路径（不填充不描边，由调用者完成）
 * @param ctx - Canvas 2D 上下文
 * @param shape - 形状类型
 * @param x - 节点中心 x 坐标
 * @param y - 节点中心 y 坐标
 * @param size - 节点半径（外接圆半径）
 */
function drawNodeShapePath(ctx: CanvasRenderingContext2D, shape: NodeShape, x: number, y: number, size: number) {
  ctx.beginPath()
  switch (shape) {
    case 'circle':
      ctx.arc(x, y, size, 0, 2 * Math.PI)
      break
    case 'diamond': {
      // 菱形：四个顶点
      const d = size * 1.15
      ctx.moveTo(x, y - d)
      ctx.lineTo(x + d, y)
      ctx.lineTo(x, y + d)
      ctx.lineTo(x - d, y)
      ctx.closePath()
      break
    }
    case 'rounded': {
      // 圆角方形
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
      // 六边形：6 个顶点
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

/** 关系类型选项，用于创建关系表单 */
const RELATION_TYPE_OPTIONS = [
  { value: 'related', label: '相关' },
  { value: 'prerequisite', label: '前置' },
  { value: 'subsequent', label: '后续' },
  { value: 'contrast', label: '对比' },
]

/** 力导向图内部节点类型（扩展自 GraphNode） */
interface ForceGraphNode extends GraphNode {
  x?: number
  y?: number
  __bckgDimensions?: [number, number]
}

/** 力导向图内部边类型 */
interface ForceGraphLink {
  id: string
  source: string | ForceGraphNode
  target: string | ForceGraphNode
  relation_type: string
  status: string
  similarity_score: number | null
}

/** 侧边栏面板类型 */
type SidebarPanel = 'suggestions' | 'nodeDetail' | 'createRelation' | null

export default function KnowledgeGraph() {
  const navigate = useNavigate()
  const graphRef = useRef<any>(null)

  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestedRelation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  /** 侧边栏状态 */
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [activePanel, setActivePanel] = useState<SidebarPanel>(null)

  /** 选中的节点 */
  const [selectedNode, setSelectedNode] = useState<ForceGraphNode | null>(null)
  /** 选中的边 */
  const [selectedLink, setSelectedLink] = useState<ForceGraphLink | null>(null)

  /** 创建关系模式 */
  const [createMode, setCreateMode] = useState(false)
  const [createFirstNode, setCreateFirstNode] = useState<ForceGraphNode | null>(null)
  const [createSecondNode, setCreateSecondNode] = useState<ForceGraphNode | null>(null)
  const [createRelationType, setCreateRelationType] = useState('related')
  const [creating, setCreating] = useState(false)

  /** 操作中状态 */
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  /** 悬停节点 */
  const [hoverNode, setHoverNode] = useState<ForceGraphNode | null>(null)
  /** 悬停边 */
  const [hoverLink, setHoverLink] = useState<ForceGraphLink | null>(null)
  /** 高亮的关系类型（点击图例触发） */
  const [highlightedRelationType, setHighlightedRelationType] = useState<string | null>(null)

  /** Minimap canvas 引用 */
  const minimapRef = useRef<HTMLCanvasElement>(null)
  /** 当前视口信息（用于 minimap 绘制视口框） */
  const viewportRef = useRef({ k: 1, x: 0, y: 0 })
  /** minimap 重绘节流标记 */
  const minimapTimerRef = useRef<number | null>(null)

  useEffect(() => {
    fetchData()
  }, [])

  /** graphData 变化时重绘 minimap（视口变化在 onZoom 回调中即时重绘） */
  useEffect(() => {
    if (graphData && graphData.nodes.length > 0) {
      // 延迟一帧等节点坐标稳定后再绘制
      const timer = setTimeout(() => drawMinimap(), 100)
      return () => clearTimeout(timer)
    }
  }, [graphData])

  // 注：原 rAF 持续动画循环（呼吸脉冲 + 蚂蚁线流动）已移除
  // 原因：d3AlphaTarget 每帧触发力导向物理重算，节点多时严重卡顿
  // 视觉升级保留静态部分（形状/首字/阴影/色条/进度条/图例高亮/缩放控件/minimap）

  async function fetchData() {
    setLoading(true)
    setError('')
    try {
      const [data, sugData] = await Promise.all([getGraphData(), getSuggestions()])
      setGraphData(data)
      setSuggestions(sugData.items || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载图谱失败')
    } finally {
      setLoading(false)
    }
  }

  /** 将 GraphData 转为 ForceGraph2D 所需格式 */
  const forceGraphData = useMemo(() => {
    if (!graphData) return { nodes: [], links: [] }
    return {
      nodes: graphData.nodes.map((n) => ({ ...n })),
      links: graphData.edges.map((e) => ({
        ...e,
        source: e.source,
        target: e.target,
      })),
    }
  }, [graphData])

  /** 计算节点大小 */
  function getNodeSize(node: ForceGraphNode): number {
    return Math.min(15, Math.max(5, 5 + (node.relation_count || 0) * 2))
  }

  /** 计算边宽度 */
  function getLinkWidth(link: ForceGraphLink): number {
    const score = (link as any).similarity_score
    if (score == null) return 1
    return Math.min(3, Math.max(1, score * 3))
  }

  /**
   * 绘制 minimap 缩略图
   * 在小 canvas 上绘制所有节点（归一化坐标）+ 边 + 当前视口框
   */
  function drawMinimap() {
    const canvas = minimapRef.current
    if (!canvas || !graphData || graphData.nodes.length === 0) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const W = canvas.width
    const H = canvas.height

    // 计算节点坐标范围（react-force-graph 运行时会注入 x/y，但类型上 GraphNode 没有）
    const nodes = graphData.nodes as ForceGraphNode[]
    const xs = nodes.map((n) => n.x || 0)
    const ys = nodes.map((n) => n.y || 0)
    let minX = Math.min(...xs), maxX = Math.max(...xs)
    let minY = Math.min(...ys), maxY = Math.max(...ys)
    // 防止所有节点重叠导致范围为 0
    if (maxX - minX < 1) { maxX = minX + 1 }
    if (maxY - minY < 1) { maxY = minY + 1 }

    // 添加 padding
    const padX = (maxX - minX) * 0.1
    const padY = (maxY - minY) * 0.1
    minX -= padX; maxX += padX
    minY -= padY; maxY += padY

    const scaleX = W / (maxX - minX)
    const scaleY = H / (maxY - minY)
    const scale = Math.min(scaleX, scaleY)
    const offsetX = (W - (maxX - minX) * scale) / 2
    const offsetY = (H - (maxY - minY) * scale) / 2

    // 坐标映射函数
    const mx = (x: number) => (x - minX) * scale + offsetX
    const my = (y: number) => (y - minY) * scale + offsetY

    // 清空 + 背景
    ctx.clearRect(0, 0, W, H)
    ctx.fillStyle = 'rgba(250, 249, 247, 0.6)'
    ctx.fillRect(0, 0, W, H)

    // 绘制边（简化为细线）
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

    // 绘制节点
    nodes.forEach((n) => {
      const color = CARD_TYPE_COLORS[n.card_type] || '#6b7280'
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(mx(n.x || 0), my(n.y || 0), 1.5, 0, 2 * Math.PI)
      ctx.fill()
    })

    // 绘制当前视口框
    const vp = viewportRef.current
    // 视口框在世界坐标系的宽高（react-force-graph 的视口换算）
    // 这里用简化估算：视口宽度 = canvas宽 / k
    const mainCanvas = graphRef.current?.current.canvas as HTMLCanvasElement | undefined
    if (mainCanvas) {
      const vw = mainCanvas.clientWidth / vp.k
      const vh = mainCanvas.clientHeight / vp.k
      // 视口左上角世界坐标 = (-vp.x / k, -vp.y / k)
      const vx = -vp.x / vp.k
      const vy = -vp.y / vp.k
      ctx.strokeStyle = 'rgba(15, 52, 96, 0.7)'
      ctx.lineWidth = 1
      ctx.setLineDash([2, 2])
      ctx.strokeRect(mx(vx), my(vy), (vw) * scale, (vh) * scale)
      ctx.setLineDash([])
    }
  }

  /** 自定义节点绘制 */
  const nodeCanvasObject = useCallback(
    (node: ForceGraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const size = getNodeSize(node)
      const color = CARD_TYPE_COLORS[node.card_type] || '#6b7280'
      const shape = CARD_TYPE_SHAPES[node.card_type] || 'circle'
      const initial = CARD_TYPE_INITIALS[node.card_type] || ''

      // 选中或悬停高亮
      const isSelected = selectedNode?.id === node.id
      const isHovered = hoverNode?.id === node.id
      const isCreateTarget =
        createMode && (createFirstNode?.id === node.id || createSecondNode?.id === node.id)
      const isHub = (node.relation_count || 0) >= 3

      // 高关联度节点静态外发光环（替代原呼吸脉冲，避免持续重绘）
      if (isHub) {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, size + 3, 0, 2 * Math.PI)
        ctx.fillStyle = `${color}1f`
        ctx.fill()
      }

      // 选中/悬停时绘制金色光环
      if (isSelected || isHovered || isCreateTarget) {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, size + 4 / globalScale, 0, 2 * Math.PI)
        ctx.fillStyle = 'rgba(201, 169, 89, 0.25)'
        ctx.fill()
      }

      // 节点主体：先设置阴影（立体感）
      ctx.save()
      ctx.shadowColor = `${color}66`
      ctx.shadowBlur = 4 / globalScale
      ctx.shadowOffsetX = 0
      ctx.shadowOffsetY = 1 / globalScale

      drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
      ctx.fillStyle = color
      ctx.fill()
      ctx.restore()

      // 选中/悬停时金色描边
      if (isSelected || isHovered || isCreateTarget) {
        drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
        ctx.strokeStyle = '#c9a959'
        ctx.lineWidth = 2 / globalScale
        ctx.stroke()
      }

      // 节点内显示类型首字（仅大节点显示）
      if (size >= 8 && initial) {
        const fontSize = Math.max(size * 0.9, 6 / globalScale)
        ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = 'rgba(255, 255, 255, 0.85)'
        ctx.fillText(initial, node.x!, node.y! + 0.5 / globalScale)
      }

      // 标签：缩放足够大或悬停/选中时显示
      if (globalScale > 0.8 || isHovered || isSelected) {
        const label = node.title
        const fontSize = Math.max(12 / globalScale, 3)
        ctx.font = `500 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
        const textWidth = ctx.measureText(label).width
        const bgWidth = textWidth + 6 / globalScale
        const bgHeight = fontSize + 4 / globalScale

        // 标签背景（圆角矩形）
        ctx.fillStyle = 'rgba(255, 255, 255, 0.92)'
        ctx.beginPath()
        ctx.roundRect(
          node.x! - bgWidth / 2,
          node.y! + size + 2 / globalScale,
          bgWidth,
          bgHeight,
          3 / globalScale,
        )
        ctx.fill()

        // 标签文字
        ctx.textAlign = 'center'
        ctx.textBaseline = 'top'
        ctx.fillStyle = isSelected || isHovered ? '#0f3460' : '#1a1a2e'
        ctx.fillText(label, node.x!, node.y! + size + 4 / globalScale)
      }

      // 记录尺寸，用于点击检测（仍用圆形包围盒，容差可接受）
      node.__bckgDimensions = [size * 2.4, size * 2.4]
    },
    [selectedNode, hoverNode, createMode, createFirstNode, createSecondNode],
  )

  /** 自定义边绘制 */
  const linkCanvasObject = useCallback(
    (link: ForceGraphLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const src = link.source as ForceGraphNode
      const tgt = link.target as ForceGraphNode
      if (src.x == null || tgt.x == null) return

      const isSuggested = link.status === 'suggested'
      const isSelected = selectedLink?.id === link.id
      const isHovered = hoverLink?.id === link.id
      const relationColor = RELATION_TYPE_COLORS[link.relation_type] || '#9a9ab0'

      // 图例高亮：非高亮类型边降低透明度
      const isDimmed = highlightedRelationType != null && link.relation_type !== highlightedRelationType

      // 选中边高亮光晕（先画一层粗的半透明线）
      if (isSelected || isHovered) {
        ctx.beginPath()
        ctx.moveTo(src.x!, src.y!)
        ctx.lineTo(tgt.x!, tgt.y!)
        ctx.setLineDash([])
        ctx.strokeStyle = `${relationColor}33`
        ctx.lineWidth = (getLinkWidth(link) * 3) / globalScale
        ctx.stroke()
      }

      // 主线
      ctx.beginPath()
      ctx.moveTo(src.x!, src.y!)
      ctx.lineTo(tgt.x!, tgt.y!)

      if (isSuggested) {
        ctx.setLineDash([4 / globalScale, 4 / globalScale])
        ctx.strokeStyle = isSelected
          ? relationColor
          : isDimmed
            ? `${relationColor}22`
            : `${relationColor}88`
      } else {
        ctx.setLineDash([])
        ctx.strokeStyle = isSelected
          ? relationColor
          : isDimmed
            ? `${relationColor}22`
            : `${relationColor}CC`
      }

      ctx.lineWidth = getLinkWidth(link) / globalScale
      ctx.stroke()
      ctx.setLineDash([])
      ctx.lineDashOffset = 0

      // hover 边时在中点显示关系类型标签
      if (isHovered || isSelected) {
        const midX = (src.x! + tgt.x!) / 2
        const midY = (src.y! + tgt.y!) / 2
        const label = RELATION_TYPE_LABELS[link.relation_type] || link.relation_type
        const fontSize = Math.max(11 / globalScale, 3)
        ctx.font = `500 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
        const textWidth = ctx.measureText(label).width
        const bgWidth = textWidth + 8 / globalScale
        const bgHeight = fontSize + 4 / globalScale

        // 标签背景胶囊
        ctx.fillStyle = 'rgba(255, 255, 255, 0.95)'
        ctx.beginPath()
        ctx.roundRect(midX - bgWidth / 2, midY - bgHeight / 2, bgWidth, bgHeight, 9999)
        ctx.fill()

        // 标签边框（用关系色）
        ctx.strokeStyle = `${relationColor}66`
        ctx.lineWidth = 1 / globalScale
        ctx.stroke()

        // 标签文字
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = relationColor
        ctx.fillText(label, midX, midY)
      }
    },
    [selectedLink, hoverLink, highlightedRelationType],
  )

  /** 节点点击 */
  function handleNodeClick(node: ForceGraphNode) {
    if (createMode) {
      if (!createFirstNode) {
        setCreateFirstNode(node)
      } else if (!createSecondNode && node.id !== createFirstNode.id) {
        setCreateSecondNode(node)
        setActivePanel('createRelation')
      }
      return
    }

    setSelectedNode(node)
    setSelectedLink(null)
    setActivePanel('nodeDetail')
    setSidebarOpen(true)
  }

  /** 边点击 */
  function handleLinkClick(link: ForceGraphLink) {
    if (createMode) return
    setSelectedLink(link)
    setSelectedNode(null)
    setSidebarOpen(true)

    if (link.status === 'suggested') {
      setActivePanel('suggestions')
    } else {
      setActivePanel(null)
    }
  }

  /** 背景点击，取消选中 */
  function handleBackgroundClick() {
    setSelectedNode(null)
    setSelectedLink(null)
    if (!createMode) {
      setActivePanel(null)
    }
  }

  /** 确认建议关系 */
  async function handleConfirm(relationId: string) {
    setActionLoading(relationId)
    try {
      await confirmRelation(relationId)
      setSuggestions((prev) => prev.filter((s) => s.id !== relationId))
      // 刷新图谱数据
      const data = await getGraphData()
      setGraphData(data)
    } catch (err) {
      alert(err instanceof Error ? err.message : '操作失败')
    } finally {
      setActionLoading(null)
    }
  }

  /** 拒绝建议关系 */
  async function handleReject(relationId: string) {
    setActionLoading(relationId)
    try {
      await rejectRelation(relationId)
      setSuggestions((prev) => prev.filter((s) => s.id !== relationId))
      const data = await getGraphData()
      setGraphData(data)
    } catch (err) {
      alert(err instanceof Error ? err.message : '操作失败')
    } finally {
      setActionLoading(null)
    }
  }

  /** 删除已确认的关系 */
  async function handleDeleteRelation(relationId: string) {
    if (!confirm('确定要删除此关系吗？')) return
    setActionLoading(relationId)
    try {
      await deleteRelation(relationId)
      setSelectedLink(null)
      const data = await getGraphData()
      setGraphData(data)
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    } finally {
      setActionLoading(null)
    }
  }

  /** 提交创建关系 */
  async function handleCreateRelation() {
    if (!createFirstNode || !createSecondNode) return
    setCreating(true)
    try {
      await createRelation(createFirstNode.id, createSecondNode.id, createRelationType)
      setCreateMode(false)
      setCreateFirstNode(null)
      setCreateSecondNode(null)
      setCreateRelationType('related')
      setActivePanel(null)
      const data = await getGraphData()
      setGraphData(data)
    } catch (err) {
      alert(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  /** 退出创建模式 */
  function cancelCreateMode() {
    setCreateMode(false)
    setCreateFirstNode(null)
    setCreateSecondNode(null)
    setCreateRelationType('related')
    setActivePanel(null)
  }

  if (loading) {
    return <LoadingSpinner text="加载知识图谱..." />
  }

  if (error) {
    return <ErrorDisplay message={error} onRetry={fetchData} />
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className="page-enter">
        <EmptyState message="暂无图谱数据" description="请先上传笔记并触发理解管道，生成知识卡片后即可查看图谱" />
      </div>
    )
  }

  const nodeCount = graphData.nodes.length
  const edgeCount = graphData.edges.length
  const suggestedCount = graphData.edges.filter((e) => e.status === 'suggested').length

  return (
    <div
      className="page-enter"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: 'calc(100vh - var(--space-xl) * 2)',
      }}
    >
      {/* 顶部栏 */}
      <div className="graph-toolbar" style={{ marginBottom: 'var(--space-md)' }}>
        <div className="graph-toolbar-left">
          <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem' }}>
            知识图谱
          </h1>
          <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem' }}>
            {nodeCount} 节点 · {edgeCount} 边
            {suggestedCount > 0 && ` · ${suggestedCount} 待审`}
          </span>
        </div>

        <div className="graph-toolbar-right">
          {/* 图例 */}
          <div className="graph-legend">
            {Object.entries(CARD_TYPE_LABELS).map(([type, label]) => (
              <span key={type} className="graph-legend-item">
                <span
                  className="graph-legend-dot"
                  style={{ background: CARD_TYPE_COLORS[type] }}
                />
                {label}
              </span>
            ))}
          </div>

          {/* 创建关系按钮 */}
          <button
            className={`graph-btn ${createMode ? 'graph-btn-active' : ''}`}
            onClick={() => {
              if (createMode) {
                cancelCreateMode()
              } else {
                setCreateMode(true)
                setActivePanel('createRelation')
                setSidebarOpen(true)
              }
            }}
          >
            {createMode ? '取消' : '创建关系'}
          </button>

          {/* 建议按钮 */}
          <button
            className="graph-btn"
            onClick={() => {
              setActivePanel(activePanel === 'suggestions' ? null : 'suggestions')
              setSidebarOpen(true)
            }}
          >
            建议
            {suggestions.length > 0 && (
              <span className="graph-badge">{suggestions.length}</span>
            )}
          </button>

          {/* 侧边栏切换 */}
          <button
            className="graph-btn"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            {sidebarOpen ? '收起' : '展开'}
          </button>
        </div>
      </div>

      {/* 创建模式提示 */}
      {createMode && (
        <div className="graph-create-hint">
          <span style={{ color: 'var(--color-accent)', fontWeight: 600 }}>●</span>
          {createFirstNode
            ? `已选择: ${createFirstNode.title}，请点击第二个节点`
            : '请点击第一个节点'}
        </div>
      )}

      {/* 主内容区 */}
      <div style={{ display: 'flex', flex: 1, gap: 'var(--space-md)', minHeight: 0 }}>
        {/* 图谱区域 */}
        <div className="graph-canvas" style={{ flex: 1 }}>
          <ForceGraph2D
            ref={graphRef}
            graphData={forceGraphData}
            nodeCanvasObject={nodeCanvasObject}
            nodePointerAreaPaint={(node: ForceGraphNode, color: string, ctx: CanvasRenderingContext2D) => {
              const size = getNodeSize(node)
              ctx.beginPath()
              ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI)
              ctx.fillStyle = color
              ctx.fill()
            }}
            linkCanvasObject={linkCanvasObject}
            linkCanvasObjectMode={() => 'replace'}
            onNodeClick={handleNodeClick}
            onNodeHover={(node: ForceGraphNode | null) => setHoverNode(node)}
            onLinkClick={handleLinkClick}
            onLinkHover={(link: ForceGraphLink | null) => setHoverLink(link)}
            onBackgroundClick={handleBackgroundClick}
            onZoom={({ k, x, y }: { k: number; x: number; y: number }) => {
              viewportRef.current = { k, x, y }
              // 节流：缩放高频触发时只在每 100ms 重绘一次 minimap
              if (minimapTimerRef.current == null) {
                minimapTimerRef.current = window.setTimeout(() => {
                  drawMinimap()
                  minimapTimerRef.current = null
                }, 100)
              }
            }}
            nodeVal={(node: ForceGraphNode) => node.relation_count}
            linkWidth={getLinkWidth}
            linkDirectionalArrowLength={3}
            linkDirectionalArrowRelPos={1}
            linkColor={(link: ForceGraphLink) => {
              const color = RELATION_TYPE_COLORS[(link as any).relation_type] || '#9a9ab0'
              return (link as any).status === 'suggested' ? `${color}88` : color
            }}
            cooldownTicks={100}
            enableNodeDrag={true}
            enableZoomInteraction={true}
            enablePanInteraction={true}
          />

          {/* Minimap 缩略图（左下角） */}
          <div className="graph-minimap">
            <canvas ref={minimapRef} width={140} height={90} />
          </div>

          {/* 缩放控件（右下角） */}
          <div className="graph-controls">
            <button
              className="graph-control-btn"
              onClick={() => {
                const fg = graphRef.current
                if (fg) {
                  const cur = viewportRef.current.k
                  fg.zoom(cur * 1.4, 400)
                }
              }}
              aria-label="放大"
              title="放大"
            >
              +
            </button>
            <button
              className="graph-control-btn"
              onClick={() => {
                const fg = graphRef.current
                if (fg) {
                  const cur = viewportRef.current.k
                  fg.zoom(cur / 1.4, 400)
                }
              }}
              aria-label="缩小"
              title="缩小"
            >
              −
            </button>
            <button
              className="graph-control-btn"
              onClick={() => {
                const fg = graphRef.current
                if (fg) fg.zoomToFit(400, 60)
              }}
              aria-label="适应屏幕"
              title="适应屏幕"
              style={{ fontSize: '0.85rem' }}
            >
              ⤢
            </button>
          </div>
        </div>

        {/* 侧边栏 */}
        {sidebarOpen && (
          <div
            style={{
              width: 300,
              flexShrink: 0,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-md)',
            }}
          >
            {/* 节点详情面板 */}
            {activePanel === 'nodeDetail' && selectedNode && (
              <div
                className="graph-panel"
                style={{
                  borderTop: `4px solid ${CARD_TYPE_COLORS[selectedNode.card_type] || '#6b7280'}`,
                }}
              >
                <div className="graph-panel-title">节点详情</div>
                <div style={{ fontSize: '0.875rem', lineHeight: 1.8 }}>
                  <div style={{ marginBottom: 'var(--space-xs)' }}>
                    <span style={{ color: 'var(--color-text-secondary)' }}>标题</span>
                    <div style={{ fontWeight: 500, marginTop: 2 }}>{selectedNode.title}</div>
                  </div>
                  <div style={{ marginBottom: 'var(--space-xs)' }}>
                    <span style={{ color: 'var(--color-text-secondary)' }}>类型</span>
                    <div style={{ marginTop: 4 }}>
                      <span
                        style={{
                          fontSize: '0.75rem',
                          padding: '2px 8px',
                          borderRadius: '9999px',
                          background: CARD_TYPE_COLORS[selectedNode.card_type] || '#6b7280',
                          color: 'white',
                          fontWeight: 500,
                        }}
                      >
                        {CARD_TYPE_LABELS[selectedNode.card_type] || selectedNode.card_type}
                      </span>
                    </div>
                  </div>
                  <div style={{ marginBottom: 'var(--space-xs)' }}>
                    <span style={{ color: 'var(--color-text-secondary)' }}>关联数</span>
                    <span style={{ marginLeft: 'var(--space-sm)', fontWeight: 600 }}>{selectedNode.relation_count}</span>
                  </div>
                  <div>
                    <span style={{ color: 'var(--color-text-secondary)' }}>来源笔记</span>
                    <span
                      style={{ cursor: 'pointer', color: 'var(--color-primary)', marginLeft: 'var(--space-sm)', fontSize: '0.8rem' }}
                      onClick={() => navigate(`/notes/${selectedNode.note_id}`)}
                    >
                      {selectedNode.note_id.slice(0, 8)}...
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* 选中的边信息 */}
            {selectedLink && (
              <div className="graph-panel">
                <div className="graph-panel-title">关系详情</div>
                <div style={{ fontSize: '0.875rem', lineHeight: 1.8 }}>
                  <div>
                    <span style={{ color: 'var(--color-text-secondary)' }}>类型：</span>
                    {RELATION_TYPE_LABELS[(selectedLink as any).relation_type] || (selectedLink as any).relation_type}
                  </div>
                  <div>
                    <span style={{ color: 'var(--color-text-secondary)' }}>状态：</span>
                    <span style={{
                      fontSize: '0.75rem',
                      padding: '2px 8px',
                      borderRadius: '9999px',
                      background: (selectedLink as any).status === 'suggested' ? 'var(--color-warning-light)' : 'var(--color-success-light)',
                      color: (selectedLink as any).status === 'suggested' ? 'var(--color-warning)' : 'var(--color-success)',
                    }}>
                      {(selectedLink as any).status === 'suggested' ? '建议' : '已确认'}
                    </span>
                  </div>
                  {(selectedLink as any).similarity_score != null && (
                    <div>
                      <span style={{ color: 'var(--color-text-secondary)' }}>相似度：</span>
                      <span style={{ fontWeight: 600 }}>{(selectedLink as any).similarity_score.toFixed(2)}</span>
                    </div>
                  )}
                </div>
                {(selectedLink as any).status === 'confirmed' && (
                  <button
                    className="btn"
                    style={{
                      fontSize: '0.8rem',
                      marginTop: 'var(--space-sm)',
                      color: 'var(--color-error)',
                      borderColor: 'var(--color-error)',
                    }}
                    onClick={() => handleDeleteRelation((selectedLink as any).id)}
                    disabled={actionLoading === (selectedLink as any).id}
                  >
                    删除关系
                  </button>
                )}
              </div>
            )}

            {/* 建议关系面板 */}
            {activePanel === 'suggestions' && (
              <div className="graph-panel">
                <div className="graph-panel-title">建议关系 ({suggestions.length})</div>
                {suggestions.length === 0 ? (
                  <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}>
                    暂无建议关系
                  </p>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
                    {suggestions.map((s) => (
                      <div key={s.id} className="graph-suggestion-card">
                        <div style={{ marginBottom: '4px', fontSize: '0.8rem' }}>
                          <strong>{s.card_1_title}</strong>
                          <span style={{ color: 'var(--color-text-secondary)', margin: '0 4px' }}>↔</span>
                          <strong>{s.card_2_title}</strong>
                        </div>
                        <div style={{ color: 'var(--color-text-secondary)', marginBottom: '6px', fontSize: '0.75rem' }}>
                          相似度: {s.similarity_score != null ? s.similarity_score.toFixed(2) : '—'}
                          {s.similarity_score != null && (
                            <div className="graph-suggestion-score-bar">
                              <div
                                className="graph-suggestion-score-bar-fill"
                                style={{ width: `${Math.round(s.similarity_score * 100)}%` }}
                              />
                            </div>
                          )}
                        </div>
                        <div style={{ display: 'flex', gap: 'var(--space-xs)' }}>
                          <button
                            className="btn btn-primary"
                            style={{ fontSize: '0.75rem', padding: '2px 8px', flex: 1 }}
                            onClick={() => handleConfirm(s.id)}
                            disabled={actionLoading === s.id}
                          >
                            确认
                          </button>
                          <button
                            className="btn"
                            style={{
                              fontSize: '0.75rem',
                              padding: '2px 8px',
                              flex: 1,
                              color: 'var(--color-error)',
                              borderColor: 'var(--color-error)',
                            }}
                            onClick={() => handleReject(s.id)}
                            disabled={actionLoading === s.id}
                          >
                            拒绝
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 创建关系面板 */}
            {activePanel === 'createRelation' && createMode && (
              <div className="graph-panel">
                <div className="graph-panel-title">创建关系</div>
                <div style={{ fontSize: '0.875rem', lineHeight: 1.8 }}>
                  <div style={{ marginBottom: 'var(--space-xs)' }}>
                    <span style={{ color: 'var(--color-text-secondary)' }}>节点 1：</span>
                    {createFirstNode?.title || '请在图谱中点击选择'}
                  </div>
                  <div style={{ marginBottom: 'var(--space-xs)' }}>
                    <span style={{ color: 'var(--color-text-secondary)' }}>节点 2：</span>
                    {createSecondNode?.title || '请在图谱中点击选择'}
                  </div>
                  <div style={{ marginTop: 'var(--space-sm)' }}>
                    <label
                      style={{
                        display: 'block',
                        color: 'var(--color-text-secondary)',
                        marginBottom: '4px',
                        fontSize: '0.75rem',
                        textTransform: 'uppercase',
                        letterSpacing: '0.05em',
                      }}
                    >
                      关系类型
                    </label>
                    <select
                      value={createRelationType}
                      onChange={(e) => setCreateRelationType(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '6px 8px',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--color-border)',
                        fontSize: '0.875rem',
                        background: 'var(--color-bg)',
                      }}
                    >
                      {RELATION_TYPE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  {createFirstNode && createSecondNode && (
                    <button
                      className="btn btn-primary"
                      style={{ width: '100%', marginTop: 'var(--space-sm)', fontSize: '0.875rem' }}
                      onClick={handleCreateRelation}
                      disabled={creating}
                    >
                      {creating ? '创建中...' : '确认创建'}
                    </button>
                  )}
                  <button
                    className="btn"
                    style={{ width: '100%', marginTop: 'var(--space-xs)', fontSize: '0.875rem' }}
                    onClick={cancelCreateMode}
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            {/* 关系类型图例（可点击高亮对应类型边） */}
            <div className="graph-panel">
              <div className="graph-panel-title">关系类型（点击高亮）</div>
              <div style={{ fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {Object.entries(RELATION_TYPE_LABELS).map(([type, label]) => (
                  <span
                    key={type}
                    className={`graph-legend-item ${highlightedRelationType === type ? 'graph-legend-item-active' : ''}`}
                    style={{ justifyContent: 'flex-start' }}
                    onClick={() => {
                      setHighlightedRelationType((prev) => (prev === type ? null : type))
                    }}
                  >
                    <span
                      className="graph-relation-line"
                      style={{ background: RELATION_TYPE_COLORS[type] }}
                    />
                    {label}
                  </span>
                ))}
                <span style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '2px 6px' }}>
                  <span
                    style={{
                      width: 20,
                      height: 0,
                      borderTop: '2px dashed var(--color-text-tertiary)',
                      display: 'inline-block',
                    }}
                  />
                  建议关系
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
