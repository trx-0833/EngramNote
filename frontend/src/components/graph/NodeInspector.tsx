import {
  cardTypeColors as CARD_TYPE_COLORS,
  FALLBACK_CATEGORY_COLOR,
  useLabels,
} from '../../utils/labels';
import type { ForceGraphNode } from './types';
// 图谱功能的类名归模块所有（overhaul-plan 5.6 序 10）：见 Graph.module.css 文件头
import styles from './Graph.module.css';
import { useTranslation } from '../../i18n';

interface NodeInspectorProps {
  node: ForceGraphNode;
  navigate: (to: string) => void;
  onViewSubgraph: (nodeId: string) => void;
  loadingSubgraph: boolean;
}

/** 节点详情面板 */
export default function NodeInspector({
  node,
  navigate,
  onViewSubgraph,
  loadingSubgraph,
}: NodeInspectorProps) {
  const { t } = useTranslation();
  const { cardTypeLabels } = useLabels();
  // 契约漂移：`note_id` 缺失时原来直接 `undefined.slice(0, 8)`，点击节点即整页崩。
  // 缺失时给出可读占位并去掉跳转（没有目标可去），其余详情照常显示。
  const noteId = node.note_id;
  return (
    <div
      className={styles.graphPanel}
      style={{
        borderTop: `4px solid ${CARD_TYPE_COLORS[node.card_type] || FALLBACK_CATEGORY_COLOR}`,
      }}
    >
      <div className={styles.graphPanelTitle}>{t('graphPage.nodeDetail')}</div>
      <div style={{ fontSize: '0.875rem', lineHeight: 1.8 }}>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>{t('graphPage.labelTitle')}</span>
          <div style={{ fontWeight: 500, marginTop: 2 }}>{node.title}</div>
        </div>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>{t('graphPage.labelType')}</span>
          <div style={{ marginTop: 4 }}>
            <span
              style={{
                fontSize: '0.75rem',
                padding: '2px 8px',
                borderRadius: 'var(--radius-full)',
                background: CARD_TYPE_COLORS[node.card_type] || FALLBACK_CATEGORY_COLOR,
                color: 'white',
                fontWeight: 500,
              }}
            >
              {cardTypeLabels[node.card_type] || node.card_type}
            </span>
          </div>
        </div>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            {t('graphPage.labelRelationCount')}
          </span>
          <span style={{ marginLeft: 'var(--space-sm)', fontWeight: 600 }}>
            {node.relation_count}
          </span>
        </div>
        <div style={{ marginBottom: 'var(--space-xs)' }}>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            {t('graphPage.labelSourceNote')}
          </span>
          {noteId ? (
            <span
              style={{
                cursor: 'pointer',
                color: 'var(--color-primary)',
                marginLeft: 'var(--space-sm)',
                fontSize: '0.8rem',
              }}
              onClick={() => navigate(`/notes/${noteId}`)}
            >
              {noteId.slice(0, 8)}...
            </span>
          ) : (
            <span
              style={{
                color: 'var(--color-text-tertiary)',
                marginLeft: 'var(--space-sm)',
                fontSize: '0.8rem',
              }}
            >
              {t('graphPage.unknownSource')}
            </span>
          )}
        </div>

        {/* 查看知识点详情按钮：跳转到卡片详情页 */}
        <button
          className="btn btn-primary"
          style={{ width: '100%', marginTop: 'var(--space-sm)', fontSize: '0.8rem' }}
          onClick={() => navigate(`/cards/${node.id}`)}
        >
          {t('graphPage.viewPointDetail')}
        </button>

        {/* 查看子图按钮 */}
        <button
          className="btn"
          style={{ width: '100%', marginTop: 'var(--space-xs)', fontSize: '0.8rem' }}
          onClick={() => onViewSubgraph(node.id)}
          disabled={loadingSubgraph}
        >
          {loadingSubgraph ? t('graphPage.loading') : t('graphPage.viewRelatedNodes')}
        </button>
      </div>
    </div>
  );
}
