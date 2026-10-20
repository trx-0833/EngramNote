/**
 * @file 图谱页顶部工具栏
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 标题与「N 节点 · M 边 · K 待审」计数（计数口径取自画布实际渲染的数据，
 * 与统计面板一致）、搜索框与结果下拉、类型过滤下拉、卡片类型图例、
 * 「创建关系 / 建议 / 收起-展开」三个按钮的类名与文案均逐字保留。
 */
import type { GraphSearchNode } from '../../api/client';
import Icon from '../../components/Icon';
// 图谱功能的类名归模块所有（overhaul-plan 5.6 序 10）：见 Graph.module.css 文件头
import styles from '../../components/graph/Graph.module.css';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变。
// `spacing="none"`：这一处标题不是页面级页头，而是工具栏左栏的第一个元素 ——
// 与下方内容的间距由 `.graphToolbar` 自己的 `marginBottom` 决定，标题不参与
import PageHeader from '../../components/PageHeader';
import {
  cardTypeColors as CARD_TYPE_COLORS,
  FALLBACK_CATEGORY_COLOR,
  useLabels,
} from '../../utils/labels';
import { useTranslation } from '../../i18n';

interface GraphToolbarProps {
  /** 画布实际渲染的节点/边数（已过滤回收站与类型过滤） */
  nodeCount: number;
  edgeCount: number;
  suggestedCount: number;
  searchKeyword: string;
  onSearchKeywordChange: (value: string) => void;
  searching: boolean;
  searchResults: GraphSearchNode[];
  /** 点击搜索结果：聚焦到该节点（居中 + 缩放 + 打开节点详情） */
  onFocusNode: (nodeId: string) => void;
  filterCardType: string | null;
  onFilterCardTypeChange: (value: string | null) => void;
  createMode: boolean;
  onToggleCreateMode: () => void;
  /** 待审建议数（按钮徽标） */
  suggestionsCount: number;
  onToggleSuggestions: () => void;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
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
  const { t } = useTranslation();
  const { cardTypeLabels } = useLabels();
  return (
    <div className={styles.graphToolbar} style={{ marginBottom: 'var(--space-md)' }}>
      <div className={styles.graphToolbarLeft}>
        {/* 页面标题（批次 C1）。计数是**并列的兄弟节点**而不是 `actions`：
            这一行后面还跟着搜索框，计数夹在标题与搜索框之间，
            原本就是三个 flex 项用 `.graphToolbarLeft` 的 gap 分开的。 */}
        <PageHeader title={t('graphPage.title')} spacing="none" />
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem' }}>
          {t('graphPage.stats', { nodes: nodeCount, edges: edgeCount })}
          {suggestedCount > 0 && t('graphPage.statsSuggested', { count: suggestedCount })}
        </span>

        {/* 搜索框 —— 批次 B3：补放大镜（此前只有 NotesList 那一处有图形） */}
        <div className={styles.graphSearchBox}>
          <Icon name="search" size={16} className={styles.graphSearchIcon} />
          <input
            type="text"
            className={styles.graphSearchInput}
            placeholder={t('graphPage.searchPlaceholder')}
            value={searchKeyword}
            onChange={(e) => onSearchKeywordChange(e.target.value)}
          />
          {searching && <span className={styles.graphSearchSpinner} />}
          {searchResults.length > 0 && (
            <div className={styles.graphSearchResults}>
              {searchResults.map((r) => (
                <div
                  key={r.id}
                  className={styles.graphSearchResultItem}
                  onClick={() => onFocusNode(r.id)}
                >
                  <span
                    className={styles.graphSearchResultDot}
                    style={{ background: CARD_TYPE_COLORS[r.card_type] || FALLBACK_CATEGORY_COLOR }}
                  />
                  <span className={styles.graphSearchResultTitle}>{r.title}</span>
                  <span className={styles.graphSearchResultType}>
                    {cardTypeLabels[r.card_type] || r.card_type}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className={styles.graphToolbarRight}>
        {/* 卡片类型过滤器。
            可访问名：这里刻意用 `aria-label` 而不是可见的 `<label>` ——
            工具栏是横向排布的一行控件，插入一个可见标签会把搜索框、过滤器、
            图例挤到第二行（布局改动，超出本轮范围）。axe 的 `select-name`
            接受 aria-label；名字是否"念出来清楚"属于人工复核（a11y-audit §4.2）。 */}
        <select
          className={styles.graphFilterSelect}
          aria-label={t('graphPage.filterByType')}
          value={filterCardType || ''}
          onChange={(e) => onFilterCardTypeChange(e.target.value || null)}
        >
          <option value="">{t('graphPage.allTypes')}</option>
          {Object.entries(cardTypeLabels).map(([type, label]) => (
            <option key={type} value={type}>
              {label}
            </option>
          ))}
        </select>

        {/* 图例 */}
        <div className={styles.graphLegend}>
          {Object.entries(cardTypeLabels).map(([type, label]) => (
            <span key={type} className={styles.graphLegendItem}>
              <span
                className={styles.graphLegendDot}
                style={{ background: CARD_TYPE_COLORS[type] }}
              />
              {label}
            </span>
          ))}
        </div>

        {/* 创建关系按钮 */}
        <button
          className={`${styles.graphBtn}${createMode ? ` ${styles.graphBtnActive}` : ''}`}
          onClick={onToggleCreateMode}
        >
          {createMode ? t('graphPage.cancel') : t('graphPage.createRelation')}
        </button>

        {/* 建议按钮 */}
        <button className={styles.graphBtn} onClick={onToggleSuggestions}>
          {t('graphPage.suggestions')}
          {suggestionsCount > 0 && <span className={styles.graphBadge}>{suggestionsCount}</span>}
        </button>

        {/* 侧边栏切换 */}
        <button className={styles.graphBtn} onClick={onToggleSidebar}>
          {sidebarOpen ? t('graphPage.collapse') : t('graphPage.expand')}
        </button>
      </div>
    </div>
  );
}
