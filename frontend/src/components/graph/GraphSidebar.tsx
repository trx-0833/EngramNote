import type { Dispatch, SetStateAction, RefObject } from 'react'
import { cardTypeColors as CARD_TYPE_COLORS } from '../../utils/labels'
import type {
  GraphStats,
  GraphData,
  NodeSubgraph,
  SuggestedRelation,
} from '../../api/client'
import NodeInspector from './NodeInspector'
import {
  type ForceGraphNode,
  type ForceGraphLink,
  type SidebarPanel,
  RELATION_TYPE_LABELS,
  RELATION_TYPE_COLORS,
  RELATION_TYPE_OPTIONS,
} from './types'

interface GraphSidebarProps {
  stats: GraphStats | null
  activePanel: SidebarPanel
  selectedNode: ForceGraphNode | null
  selectedLink: ForceGraphLink | null
  subgraphData: NodeSubgraph | null
  loadingSubgraph: boolean
  graphData: GraphData | null
  focusNode: (nodeId: string) => void
  navigate: (to: string) => void
  loadSubgraph: (nodeId: string) => void
  actionLoading: string | null
  handleConfirm: (relationId: string) => void
  handleReject: (relationId: string) => void
  handleDeleteRelation: (relationId: string) => void
  suggestions: SuggestedRelation[]
  selectedSuggestions: Set<string>
  allSuggestionsSelected: boolean
  selectAllRef: RefObject<HTMLInputElement>
  toggleSelectAll: () => void
  batchLoading: boolean
  handleBatchConfirm: () => void
  handleBatchReject: () => void
  suggesting: boolean
  suggestError: string
  handleGenerateSuggestions: () => void
  toggleSuggestion: (id: string) => void
  createMode: boolean
  createFirstNode: ForceGraphNode | null
  createSecondNode: ForceGraphNode | null
  createRelationType: string
  setCreateRelationType: Dispatch<SetStateAction<string>>
  handleCreateRelation: () => void
  creating: boolean
  cancelCreateMode: () => void
  highlightedRelationType: string | null
  setHighlightedRelationType: Dispatch<SetStateAction<string | null>>
  setActivePanel: Dispatch<SetStateAction<SidebarPanel>>
  setSubgraphData: Dispatch<SetStateAction<NodeSubgraph | null>>
}

/** 图谱统计面板 */
function StatsPanel({ stats }: { stats: GraphStats }) {
  // `relation_type_distribution` 是接口的可选尾巴：缺失时只少一段条形图。
  // 这里原来直接读 `.length`，字段一缺就抛在渲染中 → 整个图谱页被错误边界接走。
  const distribution = stats.relation_type_distribution ?? []
  const maxCount = Math.max(1, ...distribution.map((x) => x.count))

  return (
    <div className="graph-panel">
      <div className="graph-panel-title">图谱统计</div>
      <div className="graph-stats-grid">
        <div className="graph-stat-item">
          <span className="graph-stat-value">{stats.total_nodes}</span>
          <span className="graph-stat-label">节点</span>
        </div>
        <div className="graph-stat-item">
          <span className="graph-stat-value">{stats.confirmed_edges}</span>
          <span className="graph-stat-label">边</span>
        </div>
        <div className="graph-stat-item">
          <span className="graph-stat-value" style={{ color: 'var(--color-warning)' }}>
            {stats.suggested_edges}
          </span>
          <span className="graph-stat-label">待确认</span>
        </div>
        <div className="graph-stat-item">
          <span className="graph-stat-value" style={{ color: 'var(--color-text-tertiary)' }}>
            {stats.isolated_nodes}
          </span>
          <span className="graph-stat-label">孤立节点</span>
        </div>
      </div>
      {distribution.length > 0 && (
        <div style={{ marginTop: 'var(--space-xs)' }}>
          {distribution.map((d) => (
            <div key={d.relation_type} className="graph-stats-bar-row">
              <span className="graph-stats-bar-label">
                {RELATION_TYPE_LABELS[d.relation_type] || d.relation_type}
              </span>
              <div className="graph-stats-bar-track">
                <div
                  className="graph-stats-bar-fill"
                  style={{
                    width: `${Math.min(100, (d.count / maxCount) * 100)}%`,
                    background: RELATION_TYPE_COLORS[d.relation_type] || '#9a9ab0',
                  }}
                />
              </div>
              <span className="graph-stats-bar-count">{d.count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 子图面板 */
function SubgraphPanel({
  subgraphData,
  graphData,
  focusNode,
  setActivePanel,
  setSubgraphData,
}: {
  /** 调用方已归一化：`center_node` 必定存在（缺失时整块不渲染），邻居/边必定是数组 */
  subgraphData: NodeSubgraph
  graphData: GraphData | null
  focusNode: (nodeId: string) => void
  setActivePanel: Dispatch<SetStateAction<SidebarPanel>>
  setSubgraphData: Dispatch<SetStateAction<NodeSubgraph | null>>
}) {
  const neighbors = subgraphData.neighbor_nodes ?? []
  const subgraphEdges = subgraphData.edges ?? []

  return (
    <div className="graph-panel" style={{ borderTop: `4px solid ${CARD_TYPE_COLORS[subgraphData.center_node.card_type] || '#6b7280'}` }}>
      <div className="graph-panel-title">
        关联节点
        <button
          style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.75rem', color: 'var(--color-primary)' }}
          onClick={() => { setActivePanel(null); setSubgraphData(null) }}
        >
          关闭
        </button>
      </div>
      <div style={{ fontSize: '0.8rem', marginBottom: 'var(--space-sm)' }}>
        <strong>{subgraphData.center_node.title}</strong>
        <span style={{ color: 'var(--color-text-secondary)', marginLeft: 'var(--space-xs)' }}>
          → {neighbors.length} 个关联节点
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {neighbors.map((n) => {
          const edge = subgraphEdges.find(
            (e) => (e.source === n.id && e.target === subgraphData.center_node.id) ||
                   (e.target === n.id && e.source === subgraphData.center_node.id)
          )
          return (
            <div
              key={n.id}
              className="graph-neighbor-item"
              onClick={() => {
                const fn = graphData?.nodes.find((gn) => gn.id === n.id) as ForceGraphNode
                if (fn) focusNode(n.id)
              }}
            >
              <span
                className="graph-neighbor-dot"
                style={{ background: CARD_TYPE_COLORS[n.card_type] || '#6b7280' }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="graph-neighbor-title">{n.title}</div>
                {edge && (
                  <div className="graph-neighbor-rel">
                    {RELATION_TYPE_LABELS[edge.relation_type] || edge.relation_type}
                  </div>
                )}
              </div>
              <span className="graph-neighbor-count">{n.relation_count}</span>
            </div>
          )
        })}
        {neighbors.length === 0 && (
          <p style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem' }}>该节点暂无关联节点</p>
        )}
      </div>
    </div>
  )
}

/** 关系详情面板 */
function LinkDetailPanel({
  selectedLink,
  actionLoading,
  onConfirm,
  onReject,
  onDelete,
}: {
  selectedLink: ForceGraphLink
  actionLoading: string | null
  onConfirm: (relationId: string) => void
  onReject: (relationId: string) => void
  onDelete: (relationId: string) => void
}) {
  return (
    <div className="graph-panel">
      <div className="graph-panel-title">关系详情</div>
      <div style={{ fontSize: '0.875rem', lineHeight: 1.8 }}>
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>类型：</span>
          {RELATION_TYPE_LABELS[selectedLink.relation_type] || selectedLink.relation_type}
        </div>
        <div>
          <span style={{ color: 'var(--color-text-secondary)' }}>状态：</span>
          <span style={{
            fontSize: '0.75rem',
            padding: '2px 8px',
            borderRadius: '9999px',
            background: selectedLink.status === 'suggested' ? 'var(--color-warning-light)' : 'var(--color-success-light)',
            color: selectedLink.status === 'suggested' ? 'var(--color-warning)' : 'var(--color-success)',
          }}>
            {selectedLink.status === 'suggested' ? '建议' : '已确认'}
          </span>
        </div>
        {selectedLink.similarity_score != null && (
          <div>
            <span style={{ color: 'var(--color-text-secondary)' }}>相似度：</span>
            <span style={{ fontWeight: 600 }}>{selectedLink.similarity_score.toFixed(2)}</span>
          </div>
        )}
      </div>
      {selectedLink.status === 'suggested' && (
        <div style={{ display: 'flex', gap: 'var(--space-xs)', marginTop: 'var(--space-sm)' }}>
          <button
            className="btn btn-primary"
            style={{ fontSize: '0.8rem', padding: '4px 8px', flex: 1 }}
            onClick={() => onConfirm(selectedLink.id)}
            disabled={actionLoading === selectedLink.id}
          >
            确认
          </button>
          <button
            className="btn"
            style={{
              fontSize: '0.8rem',
              padding: '4px 8px',
              flex: 1,
              color: 'var(--color-error)',
              borderColor: 'var(--color-error)',
            }}
            onClick={() => onReject(selectedLink.id)}
            disabled={actionLoading === selectedLink.id}
          >
            拒绝
          </button>
        </div>
      )}
      {selectedLink.status === 'confirmed' && (
        <button
          className="btn"
          style={{
            fontSize: '0.8rem',
            marginTop: 'var(--space-sm)',
            color: 'var(--color-error)',
            borderColor: 'var(--color-error)',
          }}
          onClick={() => onDelete(selectedLink.id)}
          disabled={actionLoading === selectedLink.id}
        >
          删除关系
        </button>
      )}
    </div>
  )
}

/** 建议关系面板（含批量操作） */
function SuggestionsPanel({
  suggestions,
  selectedSuggestions,
  allSuggestionsSelected,
  selectAllRef,
  toggleSelectAll,
  batchLoading,
  onBatchConfirm,
  onBatchReject,
  suggesting,
  suggestError,
  onGenerate,
  toggleSuggestion,
  actionLoading,
  onConfirm,
  onReject,
}: {
  suggestions: SuggestedRelation[]
  selectedSuggestions: Set<string>
  allSuggestionsSelected: boolean
  selectAllRef: RefObject<HTMLInputElement>
  toggleSelectAll: () => void
  batchLoading: boolean
  onBatchConfirm: () => void
  onBatchReject: () => void
  suggesting: boolean
  suggestError: string
  onGenerate: () => void
  toggleSuggestion: (id: string) => void
  actionLoading: string | null
  onConfirm: (relationId: string) => void
  onReject: (relationId: string) => void
}) {
  return (
    <div className="graph-panel">
      <div className="graph-panel-title">
        建议关系 ({suggestions.length})
        {suggestions.length > 1 && (
          <label
            style={{
              marginLeft: 'auto',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              fontSize: '0.75rem',
              fontWeight: 'normal',
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              ref={selectAllRef}
              checked={allSuggestionsSelected}
              onChange={toggleSelectAll}
              style={{ cursor: 'pointer' }}
            />
            全选
          </label>
        )}
      </div>

      {suggestions.length > 1 && (
        <div style={{ display: 'flex', gap: 'var(--space-xs)', marginBottom: 'var(--space-sm)' }}>
          <button
            className="btn btn-primary"
            style={{ fontSize: '0.75rem', padding: '3px 8px', flex: 1 }}
            onClick={onBatchConfirm}
            disabled={selectedSuggestions.size === 0 || batchLoading}
          >
            批量确认 ({selectedSuggestions.size})
          </button>
          <button
            className="btn"
            style={{
              fontSize: '0.75rem',
              padding: '3px 8px',
              flex: 1,
              color: 'var(--color-error)',
              borderColor: 'var(--color-error)',
            }}
            onClick={onBatchReject}
            disabled={selectedSuggestions.size === 0 || batchLoading}
          >
            批量拒绝 ({selectedSuggestions.size})
          </button>
        </div>
      )}

      {suggestions.length === 0 ? (
        <div>
          <p style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-sm)' }}>
            暂无建议关系，可点击下方按钮基于嵌入向量挖掘新的潜在关联
          </p>
          <button
            className="btn btn-primary"
            style={{ width: '100%', fontSize: '0.8rem' }}
            onClick={onGenerate}
            disabled={suggesting}
          >
            {suggesting ? '生成中，卡片较多时可能需要数十秒...' : '生成相关建议'}
          </button>
          {suggestError && (
            <p role="alert" style={{ color: 'var(--color-error)', fontSize: '0.8rem', marginTop: 'var(--space-sm)' }}>
              {suggestError}
            </p>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
          {suggestions.map((s) => (
            <div key={s.id} className="graph-suggestion-card">
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-xs)' }}>
                {suggestions.length > 1 && (
                  <input
                    type="checkbox"
                    checked={selectedSuggestions.has(s.id)}
                    onChange={() => toggleSuggestion(s.id)}
                    style={{ marginTop: 3, cursor: 'pointer' }}
                  />
                )}
                <div style={{ flex: 1, cursor: 'pointer' }} onClick={() => toggleSuggestion(s.id)}>
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
                      onClick={(e) => {
                        e.stopPropagation() // 点击按钮不触发行选择
                        onConfirm(s.id)
                      }}
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
                      onClick={(e) => {
                        e.stopPropagation() // 点击按钮不触发行选择
                        onReject(s.id)
                      }}
                      disabled={actionLoading === s.id}
                    >
                      拒绝
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 创建关系面板 */
function CreateRelationPanel({
  createFirstNode,
  createSecondNode,
  createRelationType,
  setCreateRelationType,
  onSubmit,
  creating,
  onCancel,
}: {
  createFirstNode: ForceGraphNode | null
  createSecondNode: ForceGraphNode | null
  createRelationType: string
  setCreateRelationType: Dispatch<SetStateAction<string>>
  onSubmit: () => void
  creating: boolean
  onCancel: () => void
}) {
  return (
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
          <label style={{ display: 'block', color: 'var(--color-text-secondary)', marginBottom: '4px', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            关系类型
          </label>
          <select
            value={createRelationType}
            onChange={(e) => setCreateRelationType(e.target.value)}
            style={{ width: '100%', padding: '6px 8px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--color-border)', fontSize: '0.875rem', background: 'var(--color-bg)' }}
          >
            {RELATION_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
        {createFirstNode && createSecondNode && (
          <button className="btn btn-primary" style={{ width: '100%', marginTop: 'var(--space-sm)', fontSize: '0.875rem' }} onClick={onSubmit} disabled={creating}>
            {creating ? '创建中...' : '确认创建'}
          </button>
        )}
        <button className="btn" style={{ width: '100%', marginTop: 'var(--space-xs)', fontSize: '0.875rem' }} onClick={onCancel}>
          取消
        </button>
      </div>
    </div>
  )
}

/** 关系类型图例 */
function RelationLegend({
  highlightedRelationType,
  setHighlightedRelationType,
}: {
  highlightedRelationType: string | null
  setHighlightedRelationType: Dispatch<SetStateAction<string | null>>
}) {
  return (
    <div className="graph-panel">
      <div className="graph-panel-title">关系类型（点击高亮）</div>
      <div style={{ fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {Object.entries(RELATION_TYPE_LABELS).map(([type, label]) => (
          <span
            key={type}
            className={`graph-legend-item ${highlightedRelationType === type ? 'graph-legend-item-active' : ''}`}
            style={{ justifyContent: 'flex-start', cursor: 'pointer' }}
            onClick={() => setHighlightedRelationType((prev) => (prev === type ? null : type))}
          >
            <span className="graph-relation-line" style={{ background: RELATION_TYPE_COLORS[type] }} />
            {label}
          </span>
        ))}
        <span style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '2px 6px' }}>
          <span style={{ width: 20, height: 0, borderTop: '2px dashed var(--color-text-tertiary)', display: 'inline-block' }} />
          建议关系
        </span>
      </div>
    </div>
  )
}

/** 图谱侧边栏：统计 / 节点详情 / 子图 / 关系详情 / 建议 / 创建关系 / 图例 */
export default function GraphSidebar(props: GraphSidebarProps) {
  const {
    stats,
    activePanel,
    selectedNode,
    selectedLink,
    subgraphData,
    loadingSubgraph,
    graphData,
    focusNode,
    navigate,
    loadSubgraph,
    actionLoading,
    handleConfirm,
    handleReject,
    handleDeleteRelation,
    suggestions,
    selectedSuggestions,
    allSuggestionsSelected,
    selectAllRef,
    toggleSelectAll,
    batchLoading,
    handleBatchConfirm,
    handleBatchReject,
    suggesting,
    suggestError,
    handleGenerateSuggestions,
    toggleSuggestion,
    createMode,
    createFirstNode,
    createSecondNode,
    createRelationType,
    setCreateRelationType,
    handleCreateRelation,
    creating,
    cancelCreateMode,
    highlightedRelationType,
    setHighlightedRelationType,
    setActivePanel,
    setSubgraphData,
  } = props

  return (
    <div
      style={{
        width: 320,
        flexShrink: 0,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-md)',
      }}
    >
      {/* 图谱统计面板 */}
      {stats && <StatsPanel stats={stats} />}

      {/* 节点详情面板 */}
      {activePanel === 'nodeDetail' && selectedNode && (
        <NodeInspector
          node={selectedNode}
          navigate={navigate}
          onViewSubgraph={loadSubgraph}
          loadingSubgraph={loadingSubgraph}
        />
      )}

      {/* 子图面板 */}
      {activePanel === 'viewSubgraph' && subgraphData && (
        <SubgraphPanel
          subgraphData={subgraphData}
          graphData={graphData}
          focusNode={focusNode}
          setActivePanel={setActivePanel}
          setSubgraphData={setSubgraphData}
        />
      )}

      {/* 选中的边信息 */}
      {selectedLink && (
        <LinkDetailPanel
          selectedLink={selectedLink}
          actionLoading={actionLoading}
          onConfirm={handleConfirm}
          onReject={handleReject}
          onDelete={handleDeleteRelation}
        />
      )}

      {/* 建议关系面板（含批量操作） */}
      {activePanel === 'suggestions' && (
        <SuggestionsPanel
          suggestions={suggestions}
          selectedSuggestions={selectedSuggestions}
          allSuggestionsSelected={allSuggestionsSelected}
          selectAllRef={selectAllRef}
          toggleSelectAll={toggleSelectAll}
          batchLoading={batchLoading}
          onBatchConfirm={handleBatchConfirm}
          onBatchReject={handleBatchReject}
          suggesting={suggesting}
          suggestError={suggestError}
          onGenerate={handleGenerateSuggestions}
          toggleSuggestion={toggleSuggestion}
          actionLoading={actionLoading}
          onConfirm={handleConfirm}
          onReject={handleReject}
        />
      )}

      {/* 创建关系面板 */}
      {activePanel === 'createRelation' && createMode && (
        <CreateRelationPanel
          createFirstNode={createFirstNode}
          createSecondNode={createSecondNode}
          createRelationType={createRelationType}
          setCreateRelationType={setCreateRelationType}
          onSubmit={handleCreateRelation}
          creating={creating}
          onCancel={cancelCreateMode}
        />
      )}

      {/* 关系类型图例 */}
      <RelationLegend
        highlightedRelationType={highlightedRelationType}
        setHighlightedRelationType={setHighlightedRelationType}
      />
    </div>
  )
}