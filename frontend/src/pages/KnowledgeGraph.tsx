/**
 * @file 知识图谱可视化页面（增强版）
 * @description 使用 react-force-graph-2d 渲染力导向图，展示知识卡片间的关联关系。
 * 支持节点交互、建议关系确认/拒绝、手动创建关系、统计概览、搜索过滤、批量操作等功能。
 *
 * overhaul-plan 5.5：本文件已拆到 300 行以下，只保留**编排**——状态与数据归
 * `pages/knowledgegraph/` 的 hooks，绘制归纯函数，工具栏归组件。拆分是**纯提取**：
 * 计数口径、加载/失败/空三种早退的顺序、传给画布与侧边栏的 props、
 * 以及契约漂移归一化的位置都一字未动；hook 的调用顺序即拆分前 effect 的声明顺序。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import Icon from '../components/Icon';
import GraphCanvas from '../components/graph/GraphCanvas';
import GraphSidebar from '../components/graph/GraphSidebar';
import LoadingSpinner from '../components/LoadingSpinner';
import { type ForceGraphNode, type GraphForceRef } from '../components/graph/types';
import GraphToolbar from './knowledgegraph/GraphToolbar';
import { buildForceGraphData } from './knowledgegraph/buildForceGraphData';
import { renderMinimap } from './knowledgegraph/renderMinimap';
import { useCanvasObjects } from './knowledgegraph/useCanvasObjects';
import { useGraphData } from './knowledgegraph/useGraphData';
import { useGraphInteraction } from './knowledgegraph/useGraphInteraction';
import { useGraphMutations } from './knowledgegraph/useGraphMutations';
import { useGraphSearch } from './knowledgegraph/useGraphSearch';
import { useSubgraph } from './knowledgegraph/useSubgraph';
import { useSuggestionSelection } from './knowledgegraph/useSuggestionSelection';
// 图谱功能的类名归模块所有（overhaul-plan 5.6 序 10）：见 Graph.module.css 文件头
import styles from '../components/graph/Graph.module.css';

export default function KnowledgeGraph() {
  const navigate = useNavigate();
  const graphRef = useRef<GraphForceRef | undefined>(undefined);
  const graphCanvasRef = useRef<HTMLDivElement>(null);

  // ── 数据层：图谱 / 建议 / 统计（含挂载时加载与三处契约漂移归一化）──
  const graph = useGraphData();

  /** Minimap canvas 引用 */
  const minimapRef = useRef<HTMLCanvasElement>(null);
  /** 当前视口信息 */
  const viewportRef = useRef({ k: 1, x: 0, y: 0 });
  /** minimap 重绘节流标记 */
  const minimapTimerRef = useRef<number | null>(null);

  /** 类型过滤器 */
  const [filterCardType, setFilterCardType] = useState<string | null>(null);

  /** 绘制 minimap 缩略图（每次渲染重建，闭包读的是当次渲染的 graphData） */
  function drawMinimap() {
    renderMinimap(minimapRef.current, graph.graphData, viewportRef.current, graphCanvasRef.current);
  }

  /** graphData 变化时重绘 minimap（drawMinimap 每次渲染重建,加入依赖会频繁重跑,
   * 其闭包读取的是 effect 调度时刻的最新 graphData,故豁免 exhaustive-deps） */
  useEffect(() => {
    if (graph.graphData && graph.graphData.nodes.length > 0) {
      const timer = setTimeout(() => drawMinimap(), 100);
      return () => clearTimeout(timer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.graphData]);

  // ── 搜索层：关键词 + 300ms 防抖 + 结果（画布高亮与工具栏共用）──
  const search = useGraphSearch();

  // ── 交互层：侧边栏 / 选中 / 悬停 / 创建关系两步流程 ──
  const interaction = useGraphInteraction();

  // ── 批量选择：勾选集 + 全选框 indeterminate（依赖 suggestions 数量）──
  const selection = useSuggestionSelection(graph.suggestions);

  // ── 子图层：加载中心节点邻居（打开 viewSubgraph 面板）──
  const subgraph = useSubgraph({
    setActivePanel: interaction.setActivePanel,
    setSidebarOpen: interaction.setSidebarOpen,
  });

  // ── 写操作：确认/拒绝/批量/生成建议/删除/创建（失败只报错，不假装成功）──
  const mutations = useGraphMutations({
    setGraphData: graph.setGraphData,
    setStats: graph.setStats,
    setSuggestions: graph.setSuggestions,
    selectedSuggestions: selection.selectedSuggestions,
    clearSelectedSuggestions: selection.clearSelection,
    clearSelectedLink: () => interaction.setSelectedLink(null),
    createFirstNode: interaction.createFirstNode,
    createSecondNode: interaction.createSecondNode,
    createRelationType: interaction.createRelationType,
    exitCreateMode: interaction.cancelCreateMode,
  });

  /** 聚焦到搜索结果节点 */
  function focusNode(nodeId: string) {
    const fg = graphRef.current;
    if (fg) {
      const nodeData = graph.graphData?.nodes.find((n) => n.id === nodeId);
      if (nodeData) {
        const gn = nodeData as ForceGraphNode;
        if (gn.x != null && gn.y != null) {
          fg.centerAt(gn.x, gn.y, 400);
        }
        fg.zoom(3, 400);
        interaction.setSelectedNode(gn);
        interaction.setSelectedLink(null);
        interaction.setActivePanel('nodeDetail');
        interaction.setSidebarOpen(true);
      }
    }
  }

  /** 将 GraphData 转为 ForceGraph2D 所需格式（回收站过滤 + 类型过滤） */
  const forceGraphData = useMemo(
    () => buildForceGraphData(graph.graphData, filterCardType),
    [graph.graphData, filterCardType],
  );

  const { nodeCanvasObject, linkCanvasObject } = useCanvasObjects({
    selectedNode: interaction.selectedNode,
    hoverNode: interaction.hoverNode,
    createMode: interaction.createMode,
    createFirstNode: interaction.createFirstNode,
    createSecondNode: interaction.createSecondNode,
    searchResults: search.searchResults,
    selectedLink: interaction.selectedLink,
    hoverLink: interaction.hoverLink,
    highlightedRelationType: interaction.highlightedRelationType,
  });

  if (graph.loading) {
    return <LoadingSpinner text="加载知识图谱..." />;
  }

  if (graph.error) {
    return <ErrorDisplay message={graph.error} onRetry={graph.fetchData} />;
  }

  if (!graph.graphData || (graph.graphData.nodes ?? []).length === 0) {
    return (
      <div className="page-enter">
        <EmptyState
          message="暂无图谱数据"
          description="请先上传笔记并触发理解管道，生成知识卡片后即可查看图谱"
        />
      </div>
    );
  }

  // 顶部计数与画布口径一致：画布会过滤掉回收站节点（及类型过滤后的节点），
  // 原来这里用未过滤的 graphData.nodes.length，回收站里有卡片时两个数字会对不上。
  const nodeCount = forceGraphData.nodes.length;
  const edgeCount = forceGraphData.links.length;
  const suggestedCount = forceGraphData.links.filter((e) => e.status === 'suggested').length;

  return (
    <div className={`page-enter ${styles.graphPage}`}>
      {/* 顶部栏 */}
      <GraphToolbar
        nodeCount={nodeCount}
        edgeCount={edgeCount}
        suggestedCount={suggestedCount}
        searchKeyword={search.searchKeyword}
        onSearchKeywordChange={search.setSearchKeyword}
        searching={search.searching}
        searchResults={search.searchResults}
        onFocusNode={focusNode}
        filterCardType={filterCardType}
        onFilterCardTypeChange={setFilterCardType}
        createMode={interaction.createMode}
        onToggleCreateMode={interaction.toggleCreateMode}
        suggestionsCount={graph.suggestions.length}
        onToggleSuggestions={interaction.toggleSuggestionsPanel}
        sidebarOpen={interaction.sidebarOpen}
        onToggleSidebar={interaction.toggleSidebar}
      />

      {/* 创建模式提示 */}
      {interaction.createMode && (
        <div className={styles.graphCreateHint}>
          {/* 批次 B3：`\u25CF` ● 换 `<Icon name="dot" />` —— `●` 是几何图形块，
              大小与圆度都跟着字体走；`.graphCreateHint` 本来就是 flex 行，
              图标与文字的对齐由它的 `align-items: center` 承担 */}
          <Icon name="dot" size={16} style={{ color: 'var(--color-accent)' }} />
          {interaction.createFirstNode
            ? `已选择: ${interaction.createFirstNode.title}，请点击第二个节点`
            : '请点击第一个节点'}
        </div>
      )}

      {/* 主内容区 */}
      <div className={styles.graphPageMain}>
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
          onNodeClick={interaction.handleNodeClick}
          onNodeHover={interaction.handleNodeHover}
          onLinkClick={interaction.handleLinkClick}
          onLinkHover={interaction.handleLinkHover}
          onBackgroundClick={interaction.handleBackgroundClick}
        />

        {/* 侧边栏 */}
        {interaction.sidebarOpen && (
          <GraphSidebar
            stats={graph.stats}
            activePanel={interaction.activePanel}
            selectedNode={interaction.selectedNode}
            selectedLink={interaction.selectedLink}
            subgraphData={subgraph.subgraphData}
            loadingSubgraph={subgraph.loadingSubgraph}
            graphData={graph.graphData}
            focusNode={focusNode}
            navigate={navigate}
            loadSubgraph={subgraph.loadSubgraph}
            actionLoading={mutations.actionLoading}
            handleConfirm={mutations.handleConfirm}
            handleReject={mutations.handleReject}
            handleDeleteRelation={mutations.handleDeleteRelation}
            suggestions={graph.suggestions}
            selectedSuggestions={selection.selectedSuggestions}
            allSuggestionsSelected={selection.allSuggestionsSelected}
            selectAllRef={selection.selectAllRef}
            toggleSelectAll={selection.toggleSelectAll}
            batchLoading={mutations.batchLoading}
            handleBatchConfirm={mutations.handleBatchConfirm}
            handleBatchReject={mutations.handleBatchReject}
            suggesting={mutations.suggesting}
            suggestError={mutations.suggestError}
            handleGenerateSuggestions={mutations.handleGenerateSuggestions}
            toggleSuggestion={selection.toggleSuggestion}
            createMode={interaction.createMode}
            createFirstNode={interaction.createFirstNode}
            createSecondNode={interaction.createSecondNode}
            createRelationType={interaction.createRelationType}
            setCreateRelationType={interaction.setCreateRelationType}
            handleCreateRelation={mutations.handleCreateRelation}
            creating={mutations.creating}
            cancelCreateMode={interaction.cancelCreateMode}
            highlightedRelationType={interaction.highlightedRelationType}
            setHighlightedRelationType={interaction.setHighlightedRelationType}
            setActivePanel={interaction.setActivePanel}
            setSubgraphData={subgraph.setSubgraphData}
          />
        )}
      </div>
    </div>
  );
}
