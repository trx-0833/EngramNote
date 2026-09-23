/**
 * @file 知识卡片列表页面
 * @description 展示当前用户所有知识卡片，按所属笔记分组，每组可折叠/展开
 *              支持按卡片分类（常规/盲点/拓展）筛选、重点难点标记、掌握度进度条与拓展知识点生成
 */
import { useEffect, useState, useRef, useCallback } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { getKnowledgeCards, type KnowledgeCard } from '../api/client';
import { generateExtension, generateExtensionQuestions, markCard } from '../api/knowledge';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import {
  cardTypeLabels,
  cardTypeColors,
  cardCategoryLabels,
  cardCategoryColors,
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

/** 根据掌握度返回进度条颜色 */
function getMasteryColor(level: number): string {
  if (level < 40) return '#c0392b';
  if (level < 70) return '#c9a959';
  return '#2d8a56';
}

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
      const confirmed = window.confirm('拓展知识点已生成！是否立即为拓展知识点出题？');
      if (confirmed) {
        try {
          await generateExtensionQuestions(result.parent_card_id);
        } catch (err) {
          // 出题失败不阻断流程，仅提示
          toast.error(err instanceof Error ? `出题失败：${err.message}` : '出题失败');
        }
      }
      await fetchCards(searchKeyword || undefined);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '生成拓展知识点失败');
    } finally {
      setActionLoadingCardId(null);
    }
  }

  const filteredGroups = getFilteredGroups();

  return (
    <div className="page-enter">
      <div
        className="page-header-row"
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-lg)',
        }}
      >
        <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem' }}>
          知识卡片
        </h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
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
        </div>
      </div>

      {/* 分类筛选 tab */}
      <div style={{ display: 'flex', gap: 'var(--space-xs)', marginBottom: 'var(--space-md)' }}>
        {FILTER_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setFilterTab(tab.value)}
            style={{
              padding: '4px 14px',
              fontSize: '0.8rem',
              borderRadius: '9999px',
              border: '1px solid var(--color-border)',
              cursor: 'pointer',
              background: filterTab === tab.value ? 'var(--color-primary)' : 'var(--color-bg)',
              color: filterTab === tab.value ? 'white' : 'var(--color-text-secondary)',
              transition: 'all 0.15s',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 搜索栏 */}
      <div style={{ marginBottom: 'var(--space-lg)' }}>
        <input
          type="text"
          placeholder="搜索卡片标题或内容..."
          onChange={(e) => handleSearchChange(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 12px',
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
                      所以视觉不变 —— 与 F-14/F-15/F-18 的做法相同。 */}
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
                        ▶
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
                            <span title="重点" style={{ fontSize: '0.85rem', color: '#c9a959' }}>
                              ⭐
                            </span>
                          )}
                          {card.is_difficulty && (
                            <span title="难点" style={{ fontSize: '0.85rem', color: '#c0392b' }}>
                              ⚠
                            </span>
                          )}
                          <span
                            style={{
                              fontSize: '0.7rem',
                              padding: '2px 6px',
                              borderRadius: '9999px',
                              background: cardCategoryColors[card.card_category] || '#6b7280',
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
                              background: cardTypeColors[card.card_type] || '#6b7280',
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
                              ✨ 建议生成拓展知识点
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
                          {actionLoadingCardId === card.id ? '...' : '⋯'}
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
 * 「✨ 建议生成拓展知识点」那个按钮的外观复位（同上，逐项对应原来的 div）。
 *
 * `#8f7020` 是**修过的色值**，不要改回 `#c9a959`（`--color-accent`）：
 * 它在白底只有 2.26:1，而这里是一行 0.7rem 的提示文字（要求 4.5:1）——
 * 与 a11y-audit 的 F-19/F-33 是同一个"金色压浅底"的洞，`#8f7020`
 * 是同色相压深一档（白底 4.66:1）。
 */
const extensionHintStyle: React.CSSProperties = {
  display: 'block',
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
