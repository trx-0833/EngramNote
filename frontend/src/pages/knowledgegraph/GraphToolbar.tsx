/**
 * @file 图谱页顶部工具栏
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 标题与「N 节点 · M 边 · K 待审」计数（计数口径取自画布实际渲染的数据，
 * 与统计面板一致）、搜索框与结果下拉、类型过滤下拉、卡片类型图例、
 * 「创建关系 / 建议 / 收起-展开」三个按钮的类名与文案均逐字保留。
 */
import type { GraphSearchNode } from '../../api/client'
import {
  cardTypeColors as CARD_TYPE_COLORS,
  cardTypeLabels as CARD_TYPE_LABELS,
} from '../../utils/labels'

interface GraphToolbarProps {
  /** 画布实际渲染的节点/边数（已过滤回收站与类型过滤） */
  nodeCount: number
  edgeCount: number
  suggestedCount: number
  searchKeyword: string
  onSearchKeywordChange: (value: string) => void
  searching: boolean
  searchResults: GraphSearchNode[]
  /** 点击搜索结果：聚焦到该节点（居中 + 缩放 + 打开节点详情） */
  onFocusNode: (nodeId: string) => void
  filterCardType: string | null
  onFilterCardTypeChange: (value: string | null) => void
  createMode: boolean
  onToggleCreateMode: () => void
  /** 待审建议数（按钮徽标） */
  suggestionsCount: number
  onToggleSuggestions: () => void
  sidebarOpen: boolean
  onToggleSidebar: () => void
}

export default function GraphToolbar({
  nodeCount,
  edgeCount,
  suggestedCount,
  searchKeyword,
  onSearchKeywordChange,
  searching,
  searchResults,
  onFocusNode,
  filterCardType,
  onFilterCardTypeChange,
  createMode,
  onToggleCreateMode,
  suggestionsCount,
  onToggleSuggestions,
  sidebarOpen,
  onToggleSidebar,
}: GraphToolbarProps) {
  return (
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
            onChange={(e) => onSearchKeywordChange(e.target.value)}
          />
          {searching && <span className="graph-search-spinner" />}
          {searchResults.length > 0 && (
            <div className="graph-search-results">
              {searchResults.map((r) => (
                <div
                  key={r.id}
                  className="graph-search-result-item"
                  onClick={() => onFocusNode(r.id)}
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
          onChange={(e) => onFilterCardTypeChange(e.target.value || null)}
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
          onClick={onToggleCreateMode}
        >
          {createMode ? '取消' : '创建关系'}
        </button>

        {/* 建议按钮 */}
        <button
          className="graph-btn"
          onClick={onToggleSuggestions}
        >
          建议
          {suggestionsCount > 0 && (
            <span className="graph-badge">{suggestionsCount}</span>
          )}
        </button>

        {/* 侧边栏切换 */}
        <button
          className="graph-btn"
          onClick={onToggleSidebar}
        >
          {sidebarOpen ? '收起' : '展开'}
        </button>
      </div>
    </div>
  )
}
