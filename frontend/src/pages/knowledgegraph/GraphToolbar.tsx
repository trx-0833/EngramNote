/**
 * @file 图谱页顶部工具栏
 * @description 自 `pages/KnowledgeGraph.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 标题与「N 节点 · M 边 · K 待审」计数（计数口径取自画布实际渲染的数据，
 * 与统计面板一致）、搜索框与结果下拉、类型过滤下拉、卡片类型图例、
 * 「创建关系 / 建议 / 收起-展开」三个按钮的类名与文案均逐字保留。
 */
import type { GraphSearchNode } from '../../api/client';
// 图谱功能的类名归模块所有（overhaul-plan 5.6 序 10）：见 Graph.module.css 文件头
import styles from '../../components/graph/Graph.module.css';
import {
  cardTypeColors as CARD_TYPE_COLORS,
  cardTypeLabels as CARD_TYPE_LABELS,
  FALLBACK_CATEGORY_COLOR,
} from '../../utils/labels';

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
  return (
    <div className={styles.graphToolbar} style={{ marginBottom: 'var(--space-md)' }}>
      <div className={styles.graphToolbarLeft}>
        <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem' }}>
          知识图谱
        </h1>
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem' }}>
          {nodeCount} 节点 · {edgeCount} 边{suggestedCount > 0 && ` · ${suggestedCount} 待审`}
        </span>

        {/* 搜索框 */}
        <div className={styles.graphSearchBox}>
          <input
            type="text"
            className={styles.graphSearchInput}
            placeholder="搜索卡片..."
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
                    {CARD_TYPE_LABELS[r.card_type] || r.card_type}
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
          aria-label="按卡片类型筛选"
          value={filterCardType || ''}
          onChange={(e) => onFilterCardTypeChange(e.target.value || null)}
        >
          <option value="">全部类型</option>
          {Object.entries(CARD_TYPE_LABELS).map(([type, label]) => (
            <option key={type} value={type}>
              {label}
            </option>
          ))}
        </select>

        {/* 图例 */}
        <div className={styles.graphLegend}>
          {Object.entries(CARD_TYPE_LABELS).map(([type, label]) => (
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
          {createMode ? '取消' : '创建关系'}
        </button>

        {/* 建议按钮 */}
        <button className={styles.graphBtn} onClick={onToggleSuggestions}>
          建议
          {suggestionsCount > 0 && <span className={styles.graphBadge}>{suggestionsCount}</span>}
        </button>

        {/* 侧边栏切换 */}
        <button className={styles.graphBtn} onClick={onToggleSidebar}>
          {sidebarOpen ? '收起' : '展开'}
        </button>
      </div>
    </div>
  );
}
