import { cardTypeColors as CARD_TYPE_COLORS, cardTypeLabels as CARD_TYPE_LABELS } from '../../utils/labels'
import type { ForceGraphNode } from './types'

interface NodeInspectorProps {
  node: ForceGraphNode
  navigate: (to: string) => void
  onViewSubgraph: (nodeId: string) => void
  loadingSubgraph: boolean
}

/** 节点详情面板 */
export default function NodeInspector({ node, navigate, onViewSubgraph, loadingSubgraph }: NodeInspectorProps) {
  return (
    <div
      className="graph-panel"
      style={{
        borderTop: `4px solid ${CARD_TYPE_COLORS[node.card_type] || '#6b7280'}`,
      }}
    >
      <div className="graph-panel-title">节点详情</div>
      <div style={{ fontSize: '0.875rem', lineHeight: 1.8 }}>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>标题</span>
          <div style={{ fontWeight: 500, marginTop: 2 }}>{node.title}</div>
        </div>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>类型</span>
          <div style={{ marginTop: 4 }}>
            <span
              style={{
                fontSize: '0.75rem',
                padding: '2px 8px',
                borderRadius: '9999px',
                background: CARD_TYPE_COLORS[node.card_type] || '#6b7280',
                color: 'white',
                fontWeight: 500,
              }}
            >
              {CARD_TYPE_LABELS[node.card_type] || node.card_type}
            </span>
          </div>
        </div>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>关联数</span>
          <span style={{ marginLeft: 'var(--space-sm)', fontWeight: 600 }}>{node.relation_count}</span>
        </div>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>来源笔记</span>
          <span
            style={{ cursor: 'pointer', color: 'var(--color-primary)', marginLeft: 'var(--space-sm)', fontSize: '0.8rem' }}
            onClick={() => navigate(`/notes/${node.note_id}`)}
          >
            {node.note_id.slice(0, 8)}...
          </span>
        </div>

        {/* 查看知识点详情按钮：跳转到卡片详情页 */}
        <button
          className="btn btn-primary"
          style={{ width: '100%', marginTop: 'var(--space-sm)', fontSize: '0.8rem' }}
          onClick={() => navigate(`/cards/${node.id}`)}
        >
          查看知识点详情
        </button>

        {/* 查看子图按钮 */}
        <button
          className="btn"
          style={{ width: '100%', marginTop: 'var(--space-xs)', fontSize: '0.8rem' }}
          onClick={() => onViewSubgraph(node.id)}
          disabled={loadingSubgraph}
        >
          {loadingSubgraph ? '加载中...' : '查看关联节点'}
        </button>
      </div>
    </div>
  )
}