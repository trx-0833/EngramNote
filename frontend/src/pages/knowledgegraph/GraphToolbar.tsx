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
  /**
   * 当前筛选结果里"一条边都没有"的节点数（C3-③）
   *
   * ⚠️ 口径是"相对当前筛选结果"，不是接口原始数据的孤立点 —— 与画布一致，
   * 见 `buildForceGraphData` 的 `isolatedCount` 注释。
   */
  isolatedCount: number;
  /** 是否已折叠孤立点 */
  hideIsolated: boolean;
  onToggleHideIsolated: () => void;

  /**
   * 打开/关闭「疑似重复卡」面板（A1 P0-3）
   *
   * 面板自己按需拉数据（它要读向量，不该挂在常规轮询上），
   * 所以这里只传一个开关动作，不传清单本身。
   */
  onToggleDuplicates: () => void;
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
  isolatedCount,
  hideIsolated,
  onToggleHideIsolated,
  onToggleDuplicates,
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
          {/* 孤立点计数（C3-③）：关着的时候把"画布上有多少噪音"说出来。
              不放在 `hideIsolated` 打开时显示 —— 那时画布上已经没有它们了，
              再说一遍"其中 N 张未关联"会与眼前的画面矛盾。 */}
          {!hideIsolated && isolatedCount > 0 && (
            <>
              {' · '}
              <span style={{ color: 'var(--color-text-tertiary)' }}>
                {t('graphPage.isolatedHint', { count: isolatedCount })}
              </span>
            </>
          )}
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

        {/* 孤立点折叠开关（C3-③，走查报告 §3.10）
            —— 真库 3469 节点里 3028 个（87.3%）一条边都没有，力导向把画布
            铺成实心圆盘、文字重叠成噪点。这个按钮让用户能一键把噪音摘掉。
            `aria-pressed` 而不是两个不同文案的按钮：它是一个**开关**（同一件事的
            开/关），读屏用户需要知道"现在是开还是关"，而不是听到两个不同的动作名。
            计数用 `isolatedCount` 徽标 —— 与「建议」按钮的徽标同一形态。 */}
        <button
          className={`${styles.graphBtn}${hideIsolated ? ` ${styles.graphBtnActive}` : ''}`}
          onClick={onToggleHideIsolated}
          aria-pressed={hideIsolated}
        >
          {t('graphPage.hideIsolated')}
          {isolatedCount > 0 && <span className={styles.graphBadge}>{isolatedCount}</span>}
        </button>

        {/* 疑似重复按钮（A1 P0-3）
            —— 「同名闸」规定标题相同的两张卡不建边（因为它们是**重复卡片**
            而不是相关卡片），但闸门只做了"不建边"，于是这个数据问题从
            图谱里看不见了。这个按钮把清单打开给用户看。
            ⚠️ 面板里**没有**操作按钮：合并会动复习历史，属单独的一批改动。 */}
        <button className={styles.graphBtn} onClick={onToggleDuplicates}>
          {t('graphPage.duplicates')}
        </button>

        {/* 侧边栏切换 */}
        <button className={styles.graphBtn} onClick={onToggleSidebar}>
          {sidebarOpen ? t('graphPage.collapse') : t('graphPage.expand')}
        </button>
      </div>
    </div>
  );
}
