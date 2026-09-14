/**
 * @file 图谱页的交互状态：侧边栏 / 选中 / 悬停 / 创建关系两步流程
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 *
 * 两处容易拆坏、必须原样保留的细节：
 * 1. 悬停去重（§2.8 F-8）：`onNodeHover` / `onLinkHover` 在指针移动中高频触发，
 *    只有"指向的对象真的变了"才 `setState`，否则近千行页面会随鼠标每移动一次整体重渲、
 *    并因 `nodeCanvasObject` 依赖含 `hoverNode` 而重绘整张画布；
 * 2. 各点击回调设置的面板与选中项（含 `handleLinkClick` 里 `setActivePanel(null)`、
 *    `handleBackgroundClick` 里"创建模式下不清面板"的分支）一字未动。
 *
 * 5.10 移动端补丁：`sidebarOpen` 的**初始值**改为窄屏感知（宽屏仍是默认展开，
 * 与拆分前逐字一致），理由见该 state 上的注释。
 */
import { useCallback, useRef, useState } from 'react'
import type { ForceGraphLink, ForceGraphNode, SidebarPanel } from '../../components/graph/types'

/**
 * 窄屏断点：与 responsive.css 的 `@media (max-width: 768px)` 一致。
 * 两处必须同值 —— 不一致会出现"CSS 已经把它当手机（底部抽屉），JS 还当桌面（默认展开）"，
 * 首屏就有一张盖住半张画布的抽屉。
 */
const NARROW_SCREEN_MAX_WIDTH = 768

export function useGraphInteraction() {
  /**
   * 侧边栏状态
   *
   * 宽屏默认展开（与拆分前逐字一致）；**窄屏默认收起**：手机上侧边栏是固定底部抽屉
   * （`max-height: 50vh`），默认展开等于首屏就盖掉半张画布，用户得先找到「收起」才看得见图。
   * 只在首次挂载时判定：之后窗口尺寸变化不重置用户的选择（手机上转屏不该把用户
   * 手动收起的侧栏又弹开）。
   */
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > NARROW_SCREEN_MAX_WIDTH)
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

  /** 退出创建模式 */
  function cancelCreateMode() {
    setCreateMode(false)
    setCreateFirstNode(null)
    setCreateSecondNode(null)
    setCreateRelationType('related')
    setActivePanel(null)
  }

  /** 工具栏「创建关系 / 取消」按钮：进入时打开创建面板并展开侧边栏 */
  function toggleCreateMode() {
    if (createMode) {
      cancelCreateMode()
    } else {
      setCreateMode(true)
      setActivePanel('createRelation')
      setSidebarOpen(true)
    }
  }

  /** 工具栏「建议」按钮：开合建议面板（总是展开侧边栏） */
  function toggleSuggestionsPanel() {
    setActivePanel(activePanel === 'suggestions' ? null : 'suggestions')
    setSidebarOpen(true)
  }

  /** 工具栏「收起 / 展开」按钮 */
  function toggleSidebar() {
    setSidebarOpen(!sidebarOpen)
  }

  return {
    sidebarOpen,
    setSidebarOpen,
    activePanel,
    setActivePanel,
    selectedNode,
    setSelectedNode,
    selectedLink,
    setSelectedLink,
    createMode,
    createFirstNode,
    createSecondNode,
    createRelationType,
    setCreateRelationType,
    hoverNode,
    hoverLink,
    highlightedRelationType,
    setHighlightedRelationType,
    handleNodeHover,
    handleLinkHover,
    handleNodeClick,
    handleLinkClick,
    handleBackgroundClick,
    cancelCreateMode,
    toggleCreateMode,
    toggleSuggestionsPanel,
    toggleSidebar,
  }
}
