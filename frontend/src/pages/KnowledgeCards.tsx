/**
 * @file 知识卡片列表页面
 * @description 展示当前用户所有知识卡片，按所属笔记分组，每组可折叠/展开
 *              支持按卡片分类（常规/盲点/拓展）筛选、重点难点标记、掌握度进度条与拓展知识点生成
 */
import { useEffect, useState, useRef, useCallback } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { getKnowledgeCards, type KnowledgeCard } from '../api/client';
import { generateExtension, generateExtensionQuestions, markCard } from '../api/knowledge';
import Icon from '../components/Icon';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变；
// 页头那一行（标题 + 卡片数 + 图谱按钮）交给组件，窄屏换行随之进模块
import PageHeader from '../components/PageHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import {
  cardTypeLabels,
  cardTypeColors,
  cardCategoryLabels,
  cardCategoryColors,
  FALLBACK_CATEGORY_COLOR,
  getMasteryColor,
} from '../utils/labels';
import { useToast } from '../components/Toast';

interface NoteGroup {
  note_id: string;
  note_title: string;
  cards: KnowledgeCard[];
}

/** 卡片分类筛选 tab 类型 */
type CategoryFilter = 'all' | 'regular' | 'blind_spot' | 'extension';

/** 筛选选项配置 */
const FILTER_TABS: { value: CategoryFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'regular', label: '常规' },
  { value: 'blind_spot', label: '盲点' },
  { value: 'extension', label: '拓展' },
];

/* `getMasteryColor` 已于 A4 移入 `utils/labels.ts`：它原来用的是 a11y 压深**之前**
   的旧值（`<70` 那档是 `#c9a959`，白底只有 2.25:1、不达标），
   而同一份代码库里的 `difficultyColors` 早就改了 ——
   "难度"与"掌握度"表达的是同一种程度语义，色阶必须同源。 */

/** 将卡片按所属笔记分组（纯函数，模块级便于复用与测试） */
function groupByNote(cards: KnowledgeCard[]): NoteGroup[] {
  const map = new Map<string, KnowledgeCard[]>();
  for (const card of cards) {
    const list = map.get(card.note_id) || [];
    list.push(card);
    map.set(card.note_id, list);
  }
  return Array.from(map.entries())
    .map(([noteId, cards]) => ({
      note_id: noteId,
      note_title: cards[0].note_title || '未命名笔记',
      cards,
    }))
    .sort((a, b) => {
      const aTime = a.cards[0]?.created_at ?? '';
      const bTime = b.cards[0]?.created_at ?? '';
      return bTime.localeCompare(aTime);
    });
}

export default function KnowledgeCards() {
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [groups, setGroups] = useState<NoteGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set());
  const [searchKeyword, setSearchKeyword] = useState('');
  const [filterTab, setFilterTab] = useState<CategoryFilter>('all');
  const [openMenuCardId, setOpenMenuCardId] = useState<string | null>(null);
  const [actionLoadingCardId, setActionLoadingCardId] = useState<string | null>(null);
  /**
   * 待出题的拓展卡片 ID（`null` = 出题确认框关着）。
   *
   * 这里要记住的是**父卡片 ID**（`result.parent_card_id`）—— 它是
   * `generateExtension` 的返回值，只在那一刻拿得到；用户点确认时那次调用
   * 早已结束，所以必须先存起来（与 `CleaningPanel` 的块索引同理）。
   */
  const [confirmExtensionQuestions, setConfirmExtensionQuestions] = useState<string | null>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const noteId = searchParams.get('note_id') || undefined;

  const fetchCards = useCallback(
    async (keyword?: string) => {
      setLoading(true);
      try {
        const data = await getKnowledgeCards(1, 999, noteId, keyword);
        const grouped = groupByNote(data.items);
        setGroups(grouped);
        setTotal(data.total);
        setExpandedNotes(new Set(grouped.map((g) => g.note_id)));
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载失败');
      } finally {
        setLoading(false);
      }
    },
    [noteId],
  );

  // 挂载/参数变化时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchCards(searchKeyword || undefined);
  }, [noteId, fetchCards, searchKeyword]);

  // 点击页面任意位置时关闭操作菜单
  useEffect(() => {
    if (!openMenuCardId) return;
    function handleDocumentClick() {
      setOpenMenuCardId(null);
    }
    document.addEventListener('click', handleDocumentClick);
    return () => document.removeEventListener('click', handleDocumentClick);
  }, [openMenuCardId]);

  function handleSearchChange(value: string) {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setSearchKeyword(value);
    }, 300);
  }

  function toggleGroup(noteId: string) {
    setExpandedNotes((prev) => {
      const next = new Set(prev);
      if (next.has(noteId)) {
        next.delete(noteId);
      } else {
        next.add(noteId);
      }
      return next;
    });
  }

  /** 根据当前筛选 tab 在前端过滤分组 */
  function getFilteredGroups(): NoteGroup[] {
    if (filterTab === 'all') return groups;
    return groups
      .map((g) => ({ ...g, cards: g.cards.filter((c) => c.card_category === filterTab) }))
      .filter((g) => g.cards.length > 0);
  }

  /** 切换操作菜单显示状态 */
  function toggleMenu(e: React.MouseEvent, cardId: string) {
    e.stopPropagation();
    setOpenMenuCardId((prev) => (prev === cardId ? null : cardId));
  }

  /** 标记/取消标记重点或难点 */
  async function handleMark(
    e: React.MouseEvent,
    card: KnowledgeCard,
    field: 'is_key_point' | 'is_difficulty',
  ) {
    e.stopPropagation();
    const newValue = !card[field];
    setOpenMenuCardId(null);
    setActionLoadingCardId(card.id);
    try {
      const updated = await markCard(card.id, { [field]: newValue });
      setGroups((prev) =>
        prev.map((g) => ({
          ...g,
          cards: g.cards.map((c) => (c.id === card.id ? { ...c, ...updated } : c)),
        })),
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '操作失败');
    } finally {
      setActionLoadingCardId(null);
    }
  }

  /** 生成拓展知识点，成功后询问是否立即出题 */
  async function handleGenerateExtension(e: React.MouseEvent, card: KnowledgeCard) {
    e.stopPropagation();
    if (card.mastery_level < 80) return;
    setOpenMenuCardId(null);
    setActionLoadingCardId(card.id);
    try {
      const result = await generateExtension(card.id);
      // 批次 D3：原来是同步的 window.confirm，现在开确认框 ——
      // `await fetchCards(...)` 仍在同一个 try/finally 里，只是搬到了
      // "用户在框里做了选择"之后（确认与取消都要刷新，逐字保留原行为）
      setConfirmExtensionQuestions(result.parent_card_id);
      await fetchCards(searchKeyword || undefined);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '生成拓展知识点失败');
    } finally {
      setActionLoadingCardId(null);
    }
  }

  /**
   * 确认框里点了「立即出题」：逐字搬原来 `if (confirmed)` 那一支。
   *
   * 出题失败**不阻断流程**，仅提示 —— 原来的内层 `try/catch` 原样在这里，
   * 只是从"await 上下文里的内层 try"变成"回调里的 async 函数"。 */
  async function performGenerateQuestions(parentCardId: string) {
    try {
      await generateExtensionQuestions(parentCardId);
    } catch (err) {
      // 出题失败不阻断流程，仅提示
      toast.error(err instanceof Error ? `出题失败：${err.message}` : '出题失败');
    }
  }

  const filteredGroups = getFilteredGroups();

  return (
    <div className="page-enter">
      <PageHeader
        title="知识卡片"
        actions={
          <>
            <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
              共 {total} 张卡片
            </span>
            <button
              className="btn"
              style={{ fontSize: '0.8rem', padding: '4px 12px' }}
              onClick={() => navigate('/graph')}
            >
              图谱视图
            </button>
          </>
        }
      />

      {/* 分类筛选 tab —— 批次 C3：改用全局 `.filter-pill` / `.filter-pill-active`。
          这段原先是一份**手写的同款 pill**（padding / borderRadius / transition 各写一遍），
          而全站另有 4 个页面（NotesList / DailyMaterials / Upload / QuestionSets）用全局类。
          同一语义两套实现的差别是肉眼级的（padding 14 vs 12、字重 400 vs 500、
          全局版在激活态多一条金色指示线），但"改一处得记得改两处"是真实的维护成本。
          全局类住在 `learning.css`，它的 `::after` 指示线与 768px 触控规则都在同一文件里。 */}
      <div style={{ display: 'flex', gap: 'var(--space-xs)', marginBottom: 'var(--space-md)' }}>
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setFilterTab(tab.value)}
            className={`filter-pill${filterTab === tab.value ? ' filter-pill-active' : ''}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 搜索栏 —— 批次 B3：补放大镜（此前只有 NotesList 那一处有图形） */}
      <div style={{ position: 'relative', marginBottom: 'var(--space-lg)' }}>
        {/* 放大镜把左内边距吃掉 34px：图标 16px + 左 10px + 与文字留 8px。
            绝对定位 + `pointer-events: none`，所以它不会挡住输入框的点击 */}
        <Icon
          name="search"
          size={16}
          style={{
            position: 'absolute',
            left: 10,
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--color-text-tertiary)',
            pointerEvents: 'none',
          }}
        />
        <input
          type="text"
          placeholder="搜索卡片标题或内容..."
          onChange={(e) => handleSearchChange(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 12px',
            paddingLeft: 34,
            border: '1px solid var(--color-border)',
            borderRadius: '8px',
            fontSize: '0.875rem',
            background: 'var(--color-bg)',
            color: 'var(--color-text)',
            boxSizing: 'border-box',
          }}
        />
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={() => fetchCards()} />
      ) : filteredGroups.length === 0 ? (
        <EmptyState message="暂无知识卡片" description="请先上传笔记并触发理解管道" />
      ) : (
        <div>
          {filteredGroups.map((group) => (
            <div key={group.note_id} style={{ marginBottom: 'var(--space-md)' }}>
              {/* 分组头：折叠/展开是**一个真控件**。
                  ⚠️ 这里原来是 `div.card[onClick]` —— 没有 `role`、没有 `tabIndex`，
                  键盘**根本到不了**（axe 判不了这一类：它不模拟 Tab，见
                  docs/a11y-audit.md §4.1）。改法与 F-30（今日资料文件夹头）逐字同形：
                  外壳回到"盒子"，折叠行为落进 `<h2>` 里一个真 `<button aria-expanded>`
                  （WAI-ARIA 手风琴的标准写法：标题里放按钮）。
                  箭头 `aria-hidden`：展开了没有由它表达（`aria-expanded` 才是），
                  留着只会让可访问名里多一个"▶"。 */}
              <div
                className="card"
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: expandedNotes.has(group.note_id) ? 'var(--space-sm)' : 0,
                  transition: 'margin-bottom 0.15s',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                  {/* `h2` 而不是 `<strong>`：分组头是**区块级标题**（按来源笔记分组），
                      卡片的标题（下面那个 h3）住在它里面。原来两端都不是标题，
                      于是大纲是 h1「知识卡片」→ h3「卡片标题」跳级（axe 的
                      heading-order）—— 把已经存在的这个分组名提升为 h2 之后，
                      大纲变成 h1 → h2（来源笔记）→ h3（卡片），**层级是完整的**，
                      而且没有新起任何名字、没有多任何一行文字。
                      字号/字重显式钉住（与 `<strong>` 的默认外观一致），
                      所以视觉不变 —— 与 F-14/F-15/F-18 的做法相同。
                      批次 B3：`▶` 换 `<Icon name="chevron" />`（`▶` 是媒体播放符号，
                      且只活在字体里）；`.collapse-arrow` 的旋转过渡照旧由它承担。 */}
                  <h2 style={{ fontSize: '1rem', fontWeight: 700, margin: 0 }}>
                    <button
                      type="button"
                      onClick={() => toggleGroup(group.note_id)}
                      aria-expanded={expandedNotes.has(group.note_id)}
                      style={groupToggleStyle}
                    >
                      <span
                        className={`collapse-arrow ${expandedNotes.has(group.note_id) ? 'collapse-arrow-open' : ''}`}
                        aria-hidden="true"
                      >
                        <Icon name="chevron" size={16} />
                      </span>
                      <span>{group.note_title}</span>
                    </button>
                  </h2>
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    ({group.cards.length} 张卡片)
                  </span>
                </div>
              </div>

              {expandedNotes.has(group.note_id) && (
                <div
                  style={{
                    display: 'grid',
                    // min(320px, 100%)：窄屏（可用宽度 <320px）不再撑出横向滚动
                    gridTemplateColumns: 'repeat(auto-fill, minmax(min(320px, 100%), 1fr))',
                    gap: 'var(--space-md)',
                  }}
                >
                  {group.cards.map((card) => (
                    <div key={card.id} className="card card-hover" style={{ position: 'relative' }}>
                      {/* 标题与徽章区 */}
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          marginBottom: 'var(--space-sm)',
                        }}
                      >
                        <h3
                          style={{
                            fontSize: '1rem',
                            fontWeight: 600,
                            flex: 1,
                            marginRight: 'var(--space-sm)',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {/* 卡片标题是**真链接**（可右键、可新标签页、Tab 一次即达），
                              而不是"整张卡片 onClick" —— 原来外层是 `div.card[onClick]`，
                              键盘到不了。与 F-17（笔记列表卡片）/ Dashboard 的笔记卡片同形：
                              **卡片是盒子，控件在标题上**。下划线显式关掉：
                              它是"整块可点的标题链接"，不是正文里的行内链接
                              （正文链接必须带下划线，见 base.css 与 F-13）。 */}
                          <Link
                            to={`/cards/${card.id}`}
                            style={{ color: 'inherit', textDecoration: 'none' }}
                          >
                            {card.title}
                          </Link>
                        </h3>
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '4px',
                            flexShrink: 0,
                          }}
                        >
                          {card.is_key_point && (
                            <Icon name="star" size={16} title="重点" style={{ color: '#c9a959' }} />
                          )}
                          {card.is_difficulty && (
                            <Icon
                              name="warning"
                              size={16}
                              title="难点"
                              style={{ color: '#c0392b' }}
                            />
                          )}
                          <span
                            style={{
                              fontSize: '0.7rem',
                              padding: '2px 6px',
                              borderRadius: '9999px',
                              background:
                                cardCategoryColors[card.card_category] || FALLBACK_CATEGORY_COLOR,
                              color: 'white',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {cardCategoryLabels[card.card_category] || card.card_category}
                          </span>
                          <span
                            style={{
                              fontSize: '0.7rem',
                              padding: '2px 6px',
                              borderRadius: '9999px',
                              background: cardTypeColors[card.card_type] || FALLBACK_CATEGORY_COLOR,
                              color: 'white',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {cardTypeLabels[card.card_type] || card.card_type}
                          </span>
                        </div>
                      </div>

                      {/* 卡片内容 */}
                      <p
                        style={{
                          fontSize: '0.875rem',
                          color: 'var(--color-text-secondary)',
                          lineHeight: 1.5,
                          display: '-webkit-box',
                          WebkitLineClamp: 3,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}
                      >
                        {card.content}
                      </p>

                      {/* 章节信息 */}
                      {card.chapter_title && (
                        <p
                          style={{
                            fontSize: '0.75rem',
                            color: 'var(--color-text-secondary)',
                            marginTop: 'var(--space-sm)',
                          }}
                        >
                          章节: {card.chapter_title}
                        </p>
                      )}

                      {/* 掌握度进度条 */}
                      {card.mastery_level > 0 && (
                        <div style={{ marginTop: 'var(--space-sm)' }}>
                          <div
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              fontSize: '0.7rem',
                              color: 'var(--color-text-secondary)',
                              marginBottom: '2px',
                            }}
                          >
                            <span>掌握度</span>
                            <span>{Math.round(card.mastery_level)}%</span>
                          </div>
                          <div
                            style={{
                              width: '100%',
                              height: '6px',
                              background: 'var(--color-border)',
                              borderRadius: '3px',
                              overflow: 'hidden',
                            }}
                          >
                            <div
                              style={{
                                width: `${Math.min(100, Math.max(0, card.mastery_level))}%`,
                                height: '100%',
                                background: getMasteryColor(card.mastery_level),
                                transition: 'width 0.3s ease',
                              }}
                            />
                          </div>
                          {card.mastery_level >= 80 && (
                            /* 真 `<button>` 而不是 `div[onClick]`：这一行是"生成拓展知识点"的
                               入口，键盘必须到得了（原来它是个没有 role/tabIndex 的 div，
                               axe 判不了、Tab 也到不了）。外观按"一行 0.7rem 的提示文字"
                               显式复位 —— 按钮的 UA 样式（字体族/行高/内边距）必须逐个还原，
                               否则这里会长出按钮的默认外观。 */
                            <button
                              type="button"
                              onClick={(e) => handleGenerateExtension(e, card)}
                              style={extensionHintStyle}
                            >
                              {/* 批次 B3：`✨` 换 `<Icon name="ai" />` —— emoji 自带颜色，
                                  不受 `currentColor` 控制，在 0.7rem 的提示文字里比文字还重 */}
                              <Icon name="ai" size={16} />
                              建议生成拓展知识点
                            </button>
                          )}
                        </div>
                      )}

                      {/* 操作菜单 */}
                      <div
                        style={{
                          position: 'absolute',
                          bottom: 'var(--space-sm)',
                          right: 'var(--space-sm)',
                        }}
                      >
                        <button
                          onClick={(e) => toggleMenu(e, card.id)}
                          disabled={actionLoadingCardId === card.id}
                          style={{
                            border: '1px solid var(--color-border)',
                            background: 'var(--color-bg)',
                            color: 'var(--color-text-secondary)',
                            borderRadius: '6px',
                            padding: '2px 8px',
                            cursor: actionLoadingCardId === card.id ? 'not-allowed' : 'pointer',
                            fontSize: '0.8rem',
                            opacity: actionLoadingCardId === card.id ? 0.6 : 1,
                          }}
                          title="更多操作"
                        >
                          {actionLoadingCardId === card.id ? '...' : <Icon name="more" size={16} />}
                        </button>
                        {openMenuCardId === card.id && (
                          <div
                            onClick={(e) => e.stopPropagation()}
                            style={{
                              position: 'absolute',
                              bottom: '100%',
                              right: 0,
                              marginBottom: '4px',
                              background: 'var(--color-bg)',
                              border: '1px solid var(--color-border)',
                              borderRadius: '8px',
                              boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
                              padding: '4px',
                              minWidth: '140px',
                              zIndex: 10,
                            }}
                          >
                            <button
                              onClick={(e) => handleMark(e, card, 'is_key_point')}
                              style={menuItemStyle}
                            >
                              {card.is_key_point ? '取消重点' : '标记重点'}
                            </button>
                            <button
                              onClick={(e) => handleMark(e, card, 'is_difficulty')}
                              style={menuItemStyle}
                            >
                              {card.is_difficulty ? '取消难点' : '标记难点'}
                            </button>
                            <button
                              onClick={(e) => handleGenerateExtension(e, card)}
                              disabled={card.mastery_level < 80}
                              title={card.mastery_level < 80 ? '掌握度需达到 80' : ''}
                              style={{
                                ...menuItemStyle,
                                color:
                                  card.mastery_level < 80
                                    ? 'var(--color-text-secondary)'
                                    : 'var(--color-text)',
                                cursor: card.mastery_level < 80 ? 'not-allowed' : 'pointer',
                                opacity: card.mastery_level < 80 ? 0.5 : 1,
                              }}
                            >
                              生成拓展知识点
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 出题确认框（批次 D3）：**只**有标题，文案逐字保留原来
          `window.confirm` 的那一句 —— 不加 `message`，也不改按钮文案
          （原来两个按钮是浏览器给的「确定/取消」）。
          这不是危险操作（出题可重来），所以确认按钮走 `btn-primary`、不加 `danger`。
          取消 = 原来 `confirmed` 为 false 那一支：不出题，列表已经刷新过了。 */}
      <ConfirmDialog
        open={confirmExtensionQuestions !== null}
        title="拓展知识点已生成！是否立即为拓展知识点出题？"
        confirmText="确定"
        onConfirm={() => {
          const parentCardId = confirmExtensionQuestions;
          setConfirmExtensionQuestions(null);
          if (parentCardId === null) return;
          void performGenerateQuestions(parentCardId);
        }}
        onCancel={() => setConfirmExtensionQuestions(null)}
      />
    </div>
  );
}

/** 操作菜单条目的统一样式 */
const menuItemStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  textAlign: 'left',
  padding: '6px 10px',
  border: 'none',
  background: 'transparent',
  color: 'var(--color-text)',
  fontSize: '0.8rem',
  cursor: 'pointer',
  borderRadius: '4px',
};

/**
 * 分组头里那个折叠按钮的外观复位。
 *
 * 为什么要把 `font` / `color` / `background` / `border` / `padding` / `margin`
 * 全部显式写出来：`<button>` 有自己的 UA 样式（系统字体、灰底、2px 边框、
 * 居中文字、内边距），不复位的话"把 div 换成真按钮"就变成了一次改版。
 * 这里的取值逐项对应**改动前那一行 div 的实际外观**：字号/字重/颜色继承
 * 外层 `h2`（1rem / 700 / `--color-text`），背景与边框本来就没有。
 */
const groupToggleStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 'var(--space-sm)',
  margin: 0,
  padding: 0,
  border: 'none',
  background: 'none',
  font: 'inherit',
  color: 'inherit',
  textAlign: 'left',
  cursor: 'pointer',
};

/**
 * 「建议生成拓展知识点」那个按钮的外观复位（同上，逐项对应原来的 div）。
 *
 * `#8f7020` 是**修过的色值**，不要改回 `#c9a959`（`--color-accent`）：
 * 它在白底只有 2.26:1，而这里是一行 0.7rem 的提示文字（要求 4.5:1）——
 * 与 a11y-audit 的 F-19/F-33 是同一个"金色压浅底"的洞，`#8f7020`
 * 是同色相压深一档（白底 4.66:1）。
 *
 * 批次 B3：`display` 由 `block` 改 `flex` —— 按钮里多了一个 16px 的 `ai` 图标，
 * 需要 `alignItems` + `gap` 把图标与文字对齐。`flex` 仍是**块级**容器，
 * 所以"单独占一行 + `marginTop: 4px`"这两条布局行为逐字保留（不是 `inline-flex`）。
 */
const extensionHintStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 4,
  marginTop: '4px',
  fontFamily: 'inherit',
  fontSize: '0.7rem',
  lineHeight: 'inherit',
  color: '#8f7020',
  background: 'none',
  border: 'none',
  padding: 0,
  textAlign: 'left',
  cursor: 'pointer',
};
