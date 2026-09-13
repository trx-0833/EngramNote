/**
 * @file 知识图谱可视化页面（增强版）
 * @description 使用 react-force-graph-2d 渲染力导向图，展示知识卡片间的关联关系。
 * 支持节点交互、建议关系确认/拒绝、手动创建关系、统计概览、搜索过滤、批量操作等功能。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getGraphData,
  getSuggestions,
  suggestRelations,
  confirmRelation,
  rejectRelation,
  createRelation,
  deleteRelation,
  getGraphStats,
  searchGraphNodes,
  getNodeSubgraph,
  batchConfirmRelations,
  batchRejectRelations,
  type GraphData,
  type SuggestedRelation,
  type GraphStats,
  type GraphSearchNode,
  type NodeSubgraph,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import ErrorDisplay from '../components/ErrorDisplay'
import GraphCanvas from '../components/graph/GraphCanvas'
import GraphSidebar from '../components/graph/GraphSidebar'
// 卡片类型颜色/标签统一从 utils/labels.ts 读取（单一数据源），见 docs/decisions.md#F-28
import { cardTypeColors as CARD_TYPE_COLORS, cardTypeLabels as CARD_TYPE_LABELS } from '../utils/labels'
import { useToast } from '../components/Toast'
import {
  type ForceGraphNode,
  type ForceGraphLink,
  type SidebarPanel,
  type GraphForceRef,
  RELATION_TYPE_LABELS,
  RELATION_TYPE_COLORS,
  CARD_TYPE_SHAPES,
  CARD_TYPE_INITIALS,
  drawNodeShapePath,
  getNodeSize,
  getLinkWidth,
} from '../components/graph/types'

/**
 * 契约漂移兜底：接口响应少了 `nodes` / `edges`（或给的不是数组）时**在入口处**归一成空数组。
 * `nodes` 缺失曾经直接崩在 `graphData.nodes.filter`（渲染期异常 → 整页被错误边界接走），
 * 与 NoteDetail 的 `noteLinks` 是同一类缺陷。缺数组应当退化成"暂无图谱数据"，
 * 而不是白屏；下游一律读归一化后的值，无需每个使用点再补 `?.`。
 * 用 `Array.isArray` 而不是 `?? []`：字段在但类型不对（如 `{nodes: {}}`）同样要兜住。
 */
function normalizeGraphData(data: GraphData | null | undefined): GraphData | null {
  if (!data) return null
  return {
    ...data,
    nodes: Array.isArray(data.nodes) ? data.nodes : [],
    edges: Array.isArray(data.edges) ? data.edges : [],
  }
}

/** `/graph/suggestions` 契约漂移：后端若回 `{items:[...]}` 包装而不是纯数组，取内层数组 */
function normalizeSuggestions(data: unknown): SuggestedRelation[] {
  if (Array.isArray(data)) return data as SuggestedRelation[]
  const items = (data as { items?: unknown } | null | undefined)?.items
  return Array.isArray(items) ? (items as SuggestedRelation[]) : []
}

/** `relation_type_distribution` 缺失/类型不对时退化成空分布：统计面板只少一段条形图，不整页崩 */
function normalizeStats(data: GraphStats | null | undefined): GraphStats | null {
  if (!data) return null
  return {
    ...data,
    relation_type_distribution: Array.isArray(data.relation_type_distribution)
      ? data.relation_type_distribution
      : [],
  }
}

/** 子图响应契约漂移：缺 `center_node` 视为无子图（面板不出现），缺邻居数组退化成空列表 */
function normalizeSubgraph(data: NodeSubgraph | null | undefined): NodeSubgraph | null {
  if (!data || !data.center_node) return null
  return {
    ...data,
    neighbor_nodes: Array.isArray(data.neighbor_nodes) ? data.neighbor_nodes : [],
    edges: Array.isArray(data.edges) ? data.edges : [],
  }
}

export default function KnowledgeGraph() {
  const toast = useToast()
  const navigate = useNavigate()
  const graphRef = useRef<GraphForceRef | undefined>(undefined)
  const graphCanvasRef = useRef<HTMLDivElement>(null)

  const [rawGraphData, setGraphData] = useState<GraphData | null>(null)
  const [suggestions, setSuggestions] = useState<SuggestedRelation[]>([])
  const [stats, setStats] = useState<GraphStats | null>(null)
  /** 归一化后的图谱数据：`nodes` / `edges` 保证是数组（见 normalizeGraphData） */
  const graphData = useMemo(() => normalizeGraphData(rawGraphData), [rawGraphData])
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

  // 最近一次已提交的悬停 id。
  // react-force-graph 的 onNodeHover 在**指针移动过程中高频触发**，
  // 即使仍然悬停在同一个节点上也会重复回调。原实现直接 setState，
  // 于是每次鼠标移动都会让这个近千行的页面整体重渲，并因为
  // nodeCanvasObject 的 useCallback 依赖含 hoverNode 而**重绘整张画布**。
  // 这里只在"指向的节点真的变了"时才提交状态（§2.8 F-8）。
  const lastHoverNodeIdRef = useRef<string | null>(null)
  const lastHoverLinkIdRef = useRef<string | null>(null)

  const handleNodeHover = useCallback((node: ForceGraphNode | null) => {
    const id = node?.id ?? null
    if (lastHoverNodeIdRef.current === id) return
    lastHoverNodeIdRef.current = id
    setHoverNode(node)
  }, [])

  const handleLinkHover = useCallback((link: ForceGraphLink | null) => {
    const id = link ? `${link.source}-${link.target}` : null
    if (lastHoverLinkIdRef.current === id) return
    lastHoverLinkIdRef.current = id
    setHoverLink(link)
  }, [])
  /** 高亮的关系类型 */
  const [highlightedRelationType, setHighlightedRelationType] = useState<string | null>(null)

  /** 搜索 */
  const [searchKeyword, setSearchKeyword] = useState('')
  const [searchResults, setSearchResults] = useState<GraphSearchNode[]>([])
  const [searching, setSearching] = useState(false)
  const searchTimerRef = useRef<number | null>(null)

  /** 类型过滤器 */
  const [filterCardType, setFilterCardType] = useState<string | null>(null)

  /** 子图查看模式 */
  const [subgraphData, setSubgraphData] = useState<NodeSubgraph | null>(null)
  const [loadingSubgraph, setLoadingSubgraph] = useState(false)

  /** 批量选择 */
  const [selectedSuggestions, setSelectedSuggestions] = useState<Set<string>>(new Set())
  const [batchLoading, setBatchLoading] = useState(false)
  /** 手动生成相关建议中 */
  const [suggesting, setSuggesting] = useState(false)
  /** 手动生成相关建议的错误提示 */
  const [suggestError, setSuggestError] = useState('')
  /** 全选框引用，用于展示「部分选中」的 indeterminate 状态 */
  const selectAllRef = useRef<HTMLInputElement>(null)

  /** Minimap canvas 引用 */
  const minimapRef = useRef<HTMLCanvasElement>(null)
  /** 当前视口信息 */
  const viewportRef = useRef({ k: 1, x: 0, y: 0 })
  /** minimap 重绘节流标记 */
  const minimapTimerRef = useRef<number | null>(null)

  useEffect(() => {
    fetchData()
  }, [])

  /** graphData 变化时重绘 minimap（drawMinimap 每次渲染重建,加入依赖会频繁重跑,
   * 其闭包读取的是 effect 调度时刻的最新 graphData,故豁免 exhaustive-deps） */
  useEffect(() => {
    if (graphData && graphData.nodes.length > 0) {
      const timer = setTimeout(() => drawMinimap(), 100)
      return () => clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData])

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

  /** 聚焦到搜索结果节点 */
  function focusNode(nodeId: string) {
    const fg = graphRef.current
    if (fg) {
      const nodeData = graphData?.nodes.find((n) => n.id === nodeId)
      if (nodeData) {
        const gn = nodeData as ForceGraphNode
        if (gn.x != null && gn.y != null) {
          fg.centerAt(gn.x, gn.y, 400)
        }
        fg.zoom(3, 400)
        setSelectedNode(gn)
        setSelectedLink(null)
        setActivePanel('nodeDetail')
        setSidebarOpen(true)
      }
    }
  }

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

  /** 将 GraphData 转为 ForceGraph2D 所需格式 */
  const forceGraphData = useMemo(() => {
    if (!graphData) return { nodes: [], links: [] }

    // 本 memo 在组件早退之前执行：即便 graphData 缺 nodes/edges（后端只回一半、
    // 或命中旧结构缓存）也必须自身安全，所以两处都归一成数组再用。
    let nodes = graphData.nodes ?? []
    let edges = graphData.edges ?? []

    // 回收站过滤：所属笔记已进回收站的节点不渲染（关系记录后端保留，
    // 恢复后自动复原）；同时剔除指向回收站节点的边，避免 force-graph 生成幽灵节点
    const visibleIds = new Set(nodes.filter((n) => !n.note_trashed).map((n) => n.id))
    nodes = nodes.filter((n) => visibleIds.has(n.id))
    edges = edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))

    // 类型过滤
    if (filterCardType) {
      const filteredNodeIds = new Set(
        nodes.filter((n) => n.card_type === filterCardType).map((n) => n.id)
      )
      const connectedNodeIds = new Set<string>()
      edges.forEach((e) => {
        if (filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target)) {
          connectedNodeIds.add(e.source)
          connectedNodeIds.add(e.target)
        }
      })
      // 显示被过滤类型节点 + 与之关联的节点（保持图的结构完整）
      nodes = nodes.filter((n) => connectedNodeIds.has(n.id))
      edges = edges.filter(
        (e) => filteredNodeIds.has(e.source) || filteredNodeIds.has(e.target)
      )
    }

    return {
      nodes: nodes.map((n) => ({ ...n })),
      links: edges.map((e) => ({
        ...e,
        source: e.source,
        target: e.target,
      })),
    }
  }, [graphData, filterCardType])

  /**
   * 绘制 minimap 缩略图
   */
  function drawMinimap() {
    const canvas = minimapRef.current
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
    const vp = viewportRef.current
    const graphContainer = graphCanvasRef.current
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

  /** 自定义节点绘制 */
  const nodeCanvasObject = useCallback(
    (node: ForceGraphNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
      // 力导向布局过程中节点坐标可能为 undefined/NaN，跳过无效坐标避免 canvas 抛错
      if (node.x == null || node.y == null || !Number.isFinite(node.x) || !Number.isFinite(node.y)) {
        return
      }
      const size = getNodeSize(node)
      const color = CARD_TYPE_COLORS[node.card_type] || '#6b7280'
      const shape = CARD_TYPE_SHAPES[node.card_type] || 'circle'
      const initial = CARD_TYPE_INITIALS[node.card_type] || ''

      const isSelected = selectedNode?.id === node.id
      const isHovered = hoverNode?.id === node.id
      const isCreateTarget =
        createMode && (createFirstNode?.id === node.id || createSecondNode?.id === node.id)
      const isHub = (node.relation_count || 0) >= 3
      // 搜索高亮
      const isSearchHit = searchResults.some((r) => r.id === node.id)

      // 高关联度节点静态外发光环
      if (isHub) {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, size + 3, 0, 2 * Math.PI)
        ctx.fillStyle = `${color}1f`
        ctx.fill()
      }

      // 搜索高亮环
      if (isSearchHit && !isSelected && !isHovered) {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, size + 5, 0, 2 * Math.PI)
        ctx.fillStyle = 'rgba(201, 169, 89, 0.15)'
        ctx.fill()
      }

      // 选中/悬停时绘制金色光环
      if (isSelected || isHovered || isCreateTarget) {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, size + 4 / globalScale, 0, 2 * Math.PI)
        ctx.fillStyle = 'rgba(201, 169, 89, 0.25)'
        ctx.fill()
      }

      // 节点主体（玻璃珠质感：增强外发光 + 径向渐变内透光 + 反射高光点）
      ctx.save()
      ctx.shadowColor = `${color}80`
      ctx.shadowBlur = 8 / globalScale
      ctx.shadowOffsetX = 0
      ctx.shadowOffsetY = 1.5 / globalScale

      drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
      ctx.fillStyle = color
      ctx.fill()
      ctx.restore()

      // 玻璃内透光：左上高光 → 透明（径向渐变叠加，营造玻璃珠内部透光感）
      drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
      const glassGrad = ctx.createRadialGradient(
        node.x! - size * 0.35,
        node.y! - size * 0.35,
        size * 0.1,
        node.x!,
        node.y!,
        size,
      )
      glassGrad.addColorStop(0, 'rgba(255, 255, 255, 0.55)')
      glassGrad.addColorStop(0.5, 'rgba(255, 255, 255, 0.12)')
      glassGrad.addColorStop(1, 'rgba(255, 255, 255, 0)')
      ctx.fillStyle = glassGrad
      ctx.fill()

      // 玻璃珠深色边缘（轮廓）
      drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.14)'
      ctx.lineWidth = 1 / globalScale
      ctx.stroke()

      // 玻璃反射高光点（左上）
      ctx.beginPath()
      ctx.arc(
        node.x! - size * 0.32,
        node.y! - size * 0.38,
        Math.max(size * 0.22, 1.2 / globalScale),
        0,
        2 * Math.PI,
      )
      ctx.fillStyle = 'rgba(255, 255, 255, 0.85)'
      ctx.fill()

      // 选中/悬停时金色描边
      if (isSelected || isHovered || isCreateTarget) {
        drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
        ctx.strokeStyle = '#c9a959'
        ctx.lineWidth = 2 / globalScale
        ctx.stroke()
      }

      // 搜索高亮描边
      if (isSearchHit && !isSelected && !isHovered) {
        drawNodeShapePath(ctx, shape, node.x!, node.y!, size)
        ctx.strokeStyle = '#c9a959'
        ctx.lineWidth = 1.5 / globalScale
        ctx.stroke()
      }

      // 节点内显示类型首字
      if (size >= 8 && initial) {
        const fontSize = Math.max(size * 0.9, 6 / globalScale)
        ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = 'rgba(255, 255, 255, 0.85)'
        ctx.fillText(initial, node.x!, node.y! + 0.5 / globalScale)
      }

      // 标签
      if (globalScale > 0.8 || isHovered || isSelected) {
        const label = node.title
        const fontSize = Math.max(12 / globalScale, 3)
        ctx.font = `500 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
        const textWidth = ctx.measureText(label).width
        const bgWidth = textWidth + 6 / globalScale
        const bgHeight = fontSize + 4 / globalScale

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

        ctx.textAlign = 'center'
        ctx.textBaseline = 'top'
        ctx.fillStyle = isSelected || isHovered ? '#0f3460' : '#1a1a2e'
        ctx.fillText(label, node.x!, node.y! + size + 4 / globalScale)
      }

      node.__bckgDimensions = [size * 2.4, size * 2.4]
    },
    [selectedNode, hoverNode, createMode, createFirstNode, createSecondNode, searchResults],
  )

  /** 自定义边绘制 */
  const linkCanvasObject = useCallback(
    (link: ForceGraphLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const src = link.source as ForceGraphNode
      const tgt = link.target as ForceGraphNode
      if (src.x == null || tgt.x == null || !Number.isFinite(src.x) || !Number.isFinite(src.y!) || !Number.isFinite(tgt.x) || !Number.isFinite(tgt.y!)) return

      const isSuggested = link.status === 'suggested'
      const isSelected = selectedLink?.id === link.id
      const isHovered = hoverLink?.id === link.id
      const relationColor = RELATION_TYPE_COLORS[link.relation_type] || '#9a9ab0'

      const isDimmed = highlightedRelationType != null && link.relation_type !== highlightedRelationType

      // 选中边高亮光晕
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
            : `${relationColor}A8` // 墨晕感：非选中连线略淡（原: CC）
      }

      ctx.lineWidth = getLinkWidth(link) / globalScale
      ctx.stroke()
      ctx.setLineDash([])

      // hover/选中时在中点显示关系类型标签
      if (isHovered || isSelected) {
        const midX = (src.x! + tgt.x!) / 2
        const midY = (src.y! + tgt.y!) / 2
        const label = RELATION_TYPE_LABELS[link.relation_type] || link.relation_type
        const fontSize = Math.max(11 / globalScale, 3)
        ctx.font = `500 ${fontSize}px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`
        const textWidth = ctx.measureText(label).width
        const bgWidth = textWidth + 8 / globalScale
        const bgHeight = fontSize + 4 / globalScale

        ctx.fillStyle = 'rgba(255, 255, 255, 0.95)'
        ctx.beginPath()
        ctx.roundRect(midX - bgWidth / 2, midY - bgHeight / 2, bgWidth, bgHeight, 9999)
        ctx.fill()

        ctx.strokeStyle = `${relationColor}66`
        ctx.lineWidth = 1 / globalScale
        ctx.stroke()

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
    // 边详情（关系详情面板）随 selectedLink 渲染，待审/已确认边都无需切换面板
    setActivePanel(null)
  }

  /** 背景点击取消选中 */
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
      setSelectedLink(null) // 清除选中的待审边，避免详情面板残留旧状态
      const data = await getGraphData()
      setGraphData(data)
      const statsData = await getGraphStats()
      setStats(statsData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
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
      setSelectedLink(null) // 清除选中的待审边，避免详情面板残留旧状态
      const data = await getGraphData()
      setGraphData(data)
      const statsData = await getGraphStats()
      setStats(statsData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败')
    } finally {
      setActionLoading(null)
    }
  }

  /** 批量确认 */
  async function handleBatchConfirm() {
    if (selectedSuggestions.size === 0) return
    setBatchLoading(true)
    try {
      await batchConfirmRelations(Array.from(selectedSuggestions))
      setSuggestions((prev) => prev.filter((s) => !selectedSuggestions.has(s.id)))
      setSelectedSuggestions(new Set())
      const [data, statsData] = await Promise.all([getGraphData(), getGraphStats()])
      setGraphData(data)
      setStats(statsData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量操作失败')
    } finally {
      setBatchLoading(false)
    }
  }

  /** 批量拒绝 */
  async function handleBatchReject() {
    if (selectedSuggestions.size === 0) return
    if (!confirm(`确定要拒绝 ${selectedSuggestions.size} 条建议关系吗？`)) return
    setBatchLoading(true)
    try {
      await batchRejectRelations(Array.from(selectedSuggestions))
      setSuggestions((prev) => prev.filter((s) => !selectedSuggestions.has(s.id)))
      setSelectedSuggestions(new Set())
      const [data, statsData] = await Promise.all([getGraphData(), getGraphStats()])
      setGraphData(data)
      setStats(statsData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '批量操作失败')
    } finally {
      setBatchLoading(false)
    }
  }

  /** 手动生成相关建议（可能耗时数十秒，需显式触发，避免阻塞页面加载） */
  async function handleGenerateSuggestions() {
    setSuggesting(true)
    setSuggestError('')
    try {
      const result = await suggestRelations()
      const sug = await getSuggestions()
      setSuggestions(sug)
      if (result.new_count > 0) {
        const [data, statsData] = await Promise.all([getGraphData(), getGraphStats()])
        setGraphData(data)
        setStats(statsData)
      }
    } catch (err) {
      setSuggestError(err instanceof Error ? err.message : '生成建议失败')
    } finally {
      setSuggesting(false)
    }
  }

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

  /** 删除已确认的关系 */
  async function handleDeleteRelation(relationId: string) {
    if (!confirm('确定要删除此关系吗？')) return
    setActionLoading(relationId)
    try {
      await deleteRelation(relationId)
      setSelectedLink(null)
      const [data, statsData] = await Promise.all([getGraphData(), getGraphStats()])
      setGraphData(data)
      setStats(statsData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
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
      const [data, statsData] = await Promise.all([getGraphData(), getGraphStats()])
      setGraphData(data)
      setStats(statsData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '创建失败')
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

  if (!graphData || (graphData.nodes ?? []).length === 0) {
    return (
      <div className="page-enter">
        <EmptyState message="暂无图谱数据" description="请先上传笔记并触发理解管道，生成知识卡片后即可查看图谱" />
      </div>
    )
  }

  // 顶部计数与画布口径一致：画布会过滤掉回收站节点（及类型过滤后的节点），
  // 原来这里用未过滤的 graphData.nodes.length，回收站里有卡片时两个数字会对不上。
  const nodeCount = forceGraphData.nodes.length
  const edgeCount = forceGraphData.links.length
  const suggestedCount = forceGraphData.links.filter((e) => e.status === 'suggested').length

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

          {/* 搜索框 */}
          <div className="graph-search-box">
            <input
              type="text"
              className="graph-search-input"
              placeholder="搜索卡片..."
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
            />
            {searching && <span className="graph-search-spinner" />}
            {searchResults.length > 0 && (
              <div className="graph-search-results">
                {searchResults.map((r) => (
                  <div
                    key={r.id}
                    className="graph-search-result-item"
                    onClick={() => focusNode(r.id)}
                  >
                    <span
                      className="graph-search-result-dot"
                      style={{ background: CARD_TYPE_COLORS[r.card_type] || '#6b7280' }}
                    />
                    <span className="graph-search-result-title">{r.title}</span>
                    <span className="graph-search-result-type">
                      {CARD_TYPE_LABELS[r.card_type] || r.card_type}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="graph-toolbar-right">
          {/* 卡片类型过滤器 */}
          <select
            className="graph-filter-select"
            value={filterCardType || ''}
            onChange={(e) => setFilterCardType(e.target.value || null)}
          >
            <option value="">全部类型</option>
            {Object.entries(CARD_TYPE_LABELS).map(([type, label]) => (
              <option key={type} value={type}>{label}</option>
            ))}
          </select>

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
              if (createMode) cancelCreateMode()
              else {
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
        <GraphCanvas
          graphRef={graphRef}
          graphCanvasRef={graphCanvasRef}
          minimapRef={minimapRef}
          viewportRef={viewportRef}
          minimapTimerRef={minimapTimerRef}
          drawMinimap={drawMinimap}
          forceGraphData={forceGraphData}
          nodeCanvasObject={nodeCanvasObject}
          linkCanvasObject={linkCanvasObject}
          onNodeClick={handleNodeClick}
          onNodeHover={handleNodeHover}
          onLinkClick={handleLinkClick}
          onLinkHover={handleLinkHover}
          onBackgroundClick={handleBackgroundClick}
        />

        {/* 侧边栏 */}
        {sidebarOpen && (
          <GraphSidebar
            stats={stats}
            activePanel={activePanel}
            selectedNode={selectedNode}
            selectedLink={selectedLink}
            subgraphData={subgraphData}
            loadingSubgraph={loadingSubgraph}
            graphData={graphData}
            focusNode={focusNode}
            navigate={navigate}
            loadSubgraph={loadSubgraph}
            actionLoading={actionLoading}
            handleConfirm={handleConfirm}
            handleReject={handleReject}
            handleDeleteRelation={handleDeleteRelation}
            suggestions={suggestions}
            selectedSuggestions={selectedSuggestions}
            allSuggestionsSelected={allSuggestionsSelected}
            selectAllRef={selectAllRef}
            toggleSelectAll={toggleSelectAll}
            batchLoading={batchLoading}
            handleBatchConfirm={handleBatchConfirm}
            handleBatchReject={handleBatchReject}
            suggesting={suggesting}
            suggestError={suggestError}
            handleGenerateSuggestions={handleGenerateSuggestions}
            toggleSuggestion={toggleSuggestion}
            createMode={createMode}
            createFirstNode={createFirstNode}
            createSecondNode={createSecondNode}
            createRelationType={createRelationType}
            setCreateRelationType={setCreateRelationType}
            handleCreateRelation={handleCreateRelation}
            creating={creating}
            cancelCreateMode={cancelCreateMode}
            highlightedRelationType={highlightedRelationType}
            setHighlightedRelationType={setHighlightedRelationType}
            setActivePanel={setActivePanel}
            setSubgraphData={setSubgraphData}
          />
        )}
      </div>
    </div>
  )
}