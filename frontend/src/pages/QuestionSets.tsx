/**
 * @file 问题集列表页面
 * @description 展示当前用户所有问答题，按所属笔记分组，每组可折叠/展开
 * 仿照 KnowledgeCards 页面结构，将每份文档的问答题集展示出来
 */
import { useEffect, useState, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getQuestions, type QuizItem } from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变；
// 页头那一行（标题 + 题量计数）交给组件，窄屏换行随之进模块
import PageHeader from '../components/PageHeader';
import {
  questionTypeLabels,
  questionTypeColors,
  difficultyLabels,
  difficultyColors,
  FALLBACK_CATEGORY_COLOR,
} from '../utils/labels';

function parseOptions(optionsStr: string | null): string[] {
  if (!optionsStr) return [];
  try {
    const parsed = JSON.parse(optionsStr);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

interface NoteGroup {
  note_id: string;
  note_title: string;
  questions: QuizItem[];
}

/** 将题目按所属笔记分组（纯函数，模块级便于复用与测试） */
function groupByNote(questions: QuizItem[]): NoteGroup[] {
  const map = new Map<string, QuizItem[]>();
  for (const q of questions) {
    const list = map.get(q.note_id) || [];
    list.push(q);
    map.set(q.note_id, list);
  }
  return Array.from(map.entries())
    .map(([noteId, questions]) => ({
      note_id: noteId,
      note_title: questions[0].note_title || '未命名笔记',
      questions,
    }))
    .sort((a, b) => {
      const aTime = a.questions[0]?.created_at ?? '';
      const bTime = b.questions[0]?.created_at ?? '';
      return bTime.localeCompare(aTime);
    });
}

export default function QuestionSets() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [groups, setGroups] = useState<NoteGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set());
  const [filterType, setFilterType] = useState<string>('all');
  const [filterDifficulty, setFilterDifficulty] = useState<string>('all');
  const [searchKeyword, setSearchKeyword] = useState('');
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const noteId = searchParams.get('note_id') || undefined;

  const fetchQuestions = useCallback(
    async (keyword?: string) => {
      setLoading(true);
      try {
        // 后端 page_size 上限为 100，需分页加载全部题目。
        //
        // 终止条件必须有多重兜底（见 docs/overhaul-plan.md §2.8 F-3）：
        // 原实现只写 `while (allItems.length < totalCount)`，一旦后端返回的
        // total 与可返回条数不一致（分页越界、软删过滤口径不同、并发写入等），
        // 这个循环**永远不会结束**，会持续向后端发请求并把页面卡在 loading。
        // 现在加上"最大页数"与"空页即停"两道硬兜底。
        const MAX_PAGES = 100;
        const allItems: QuizItem[] = [];
        let page = 1;
        const pageSize = 100;
        let totalCount = 0;
        let truncated = false;

        while (page <= MAX_PAGES) {
          const data = await getQuestions(page, pageSize, noteId, keyword);
          const items = data.items || [];
          allItems.push(...items);
          totalCount = data.total ?? allItems.length;

          // 空页说明已经取完（后端 total 可能不准），立即停止
          if (items.length === 0) break;
          // 已取够 total 声明的数量
          if (allItems.length >= totalCount) break;

          page++;
        }

        if (page > MAX_PAGES && allItems.length < totalCount) {
          truncated = true;
          console.warn(
            `[QuestionSets] 分页达到上限 ${MAX_PAGES} 页，已加载 ${allItems.length}/${totalCount} 条`,
          );
        }

        const grouped = groupByNote(allItems);
        setGroups(grouped);
        setTotal(truncated ? allItems.length : totalCount);
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
    fetchQuestions(searchKeyword || undefined);
  }, [noteId, fetchQuestions, searchKeyword]);

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

  function filterQuestions(questions: QuizItem[]): QuizItem[] {
    return questions.filter((q) => {
      if (filterType !== 'all' && q.question_type !== filterType) return false;
      if (filterDifficulty !== 'all' && q.difficulty !== filterDifficulty) return false;
      return true;
    });
  }

  const totalFiltered = groups.reduce((sum, g) => sum + filterQuestions(g.questions).length, 0);

  return (
    <div className="page-enter">
      <PageHeader
        title="问题集"
        spacing="md"
        actions={
          <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
            共 {total} 道题
            {filterType !== 'all' || filterDifficulty !== 'all'
              ? `，筛选后 ${totalFiltered} 道`
              : ''}
          </span>
        }
      />

      {/* 搜索栏 */}
      <div style={{ marginBottom: 'var(--space-md)' }}>
        <input
          type="text"
          placeholder="搜索题目内容..."
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

      {/* 筛选栏 */}
      <div
        style={{
          display: 'flex',
          gap: 'var(--space-sm)',
          marginBottom: 'var(--space-lg)',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>题型：</span>
          {[
            { value: 'all', label: '全部' },
            { value: 'choice', label: '选择' },
            { value: 'fill_blank', label: '填空' },
            { value: 'short_answer', label: '简答' },
          ].map((opt) => (
            <button
              key={opt.value}
              className={`filter-pill ${filterType === opt.value ? 'filter-pill-active' : ''}`}
              onClick={() => setFilterType(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>难度：</span>
          {[
            { value: 'all', label: '全部' },
            { value: 'easy', label: '简单' },
            { value: 'medium', label: '中等' },
            { value: 'hard', label: '困难' },
          ].map((opt) => (
            <button
              key={opt.value}
              className={`filter-pill ${filterDifficulty === opt.value ? 'filter-pill-active' : ''}`}
              onClick={() => setFilterDifficulty(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchQuestions} />
      ) : groups.length === 0 ? (
        <EmptyState message="暂无题目" description="请先上传笔记并触发理解管道生成题目" />
      ) : (
        <div>
          {groups.map((group) => {
            const filtered = filterQuestions(group.questions);
            if (filtered.length === 0) return null;
            return (
              <div key={group.note_id} style={{ marginBottom: 'var(--space-md)' }}>
                {/* 分组头：折叠/展开是**一个真控件**。
                    ⚠️ 这里原来是 `div.card[onClick]`（没有 role/tabIndex，键盘到不了），
                    里面还嵌着「查看笔记」真按钮。改法与 F-30 / KnowledgeCards 分组头同形：
                    外壳回到"盒子"，折叠行为落进真 `<button aria-expanded>`，
                    「查看笔记」是它的**兄弟**（不再是被点区域的后代）。
                    `stopPropagation` 留着（外层已经没有 onClick 了）：它同时对
                    "点空白处"这类调用有意义，删掉属于顺手重构。 */}
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
                    <button
                      type="button"
                      onClick={() => toggleGroup(group.note_id)}
                      aria-expanded={expandedNotes.has(group.note_id)}
                      style={groupToggleStyle}
                    >
                      {/* 箭头只表达外观：展开状态由 `aria-expanded` 承担 */}
                      <span
                        className={`collapse-arrow ${expandedNotes.has(group.note_id) ? 'collapse-arrow-open' : ''}`}
                        aria-hidden="true"
                      >
                        ▶
                      </span>
                      <strong>{group.note_title}</strong>
                    </button>
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                      ({filtered.length} 道题)
                    </span>
                  </div>
                  <button
                    className="btn btn-secondary"
                    style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/notes/${group.note_id}`);
                    }}
                  >
                    查看笔记
                  </button>
                </div>

                {expandedNotes.has(group.note_id) && (
                  <div
                    style={{
                      display: 'grid',
                      // min(360px, 100%)：360px 屏上原本必然横向溢出（可用宽度不足 360px）
                      gridTemplateColumns: 'repeat(auto-fill, minmax(min(360px, 100%), 1fr))',
                      gap: 'var(--space-md)',
                    }}
                  >
                    {filtered.map((q) => (
                      <div key={q.id} className="card card-hover">
                        {/* 题目头部：题型 + 难度标签 */}
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginBottom: 'var(--space-sm)',
                          }}
                        >
                          <div style={{ display: 'flex', gap: '4px' }}>
                            <span
                              style={{
                                fontSize: '0.75rem',
                                padding: '2px 8px',
                                borderRadius: '9999px',
                                background:
                                  questionTypeColors[q.question_type] || FALLBACK_CATEGORY_COLOR,
                                color: 'white',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {questionTypeLabels[q.question_type] || q.question_type}
                            </span>
                            <span
                              style={{
                                fontSize: '0.75rem',
                                padding: '2px 8px',
                                borderRadius: '9999px',
                                background:
                                  difficultyColors[q.difficulty] || FALLBACK_CATEGORY_COLOR,
                                color: 'white',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {difficultyLabels[q.difficulty] || q.difficulty}
                            </span>
                          </div>
                        </div>

                        {/* 题目内容 */}
                        <p
                          style={{
                            fontSize: '0.95rem',
                            lineHeight: 1.6,
                            marginBottom: 'var(--space-sm)',
                            display: '-webkit-box',
                            WebkitLineClamp: 4,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                          }}
                        >
                          {q.question}
                        </p>

                        {/* 选择题选项 */}
                        {q.question_type === 'choice' && q.options && (
                          <div style={{ marginBottom: 'var(--space-sm)' }}>
                            {parseOptions(q.options).map((opt, i) => (
                              <div
                                key={i}
                                style={{
                                  fontSize: '0.85rem',
                                  color: 'var(--color-text-secondary)',
                                  padding: '2px 0',
                                  paddingLeft: 'var(--space-sm)',
                                }}
                              >
                                {opt}
                              </div>
                            ))}
                          </div>
                        )}

                        {/* 答案（默认折叠，点击展开） */}
                        <AnswerSection answer={q.answer} explanation={q.explanation} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/**
 * 分组头里那个折叠按钮的外观复位。
 *
 * `<button>` 有自己的 UA 样式（系统字体、灰底、2px 边框、居中文字、内边距），
 * 不复位的话"把 div 换成真按钮"就变成了一次改版。下面的取值逐项对应
 * **改动前那一行 div 的实际外观**：字号/字重/颜色继承外层（`<strong>` 的
 * 700 与正文的 1em 都是继承来的），背景与边框本来就没有。
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
 * 答案折叠组件：默认隐藏答案和解析，点击按钮展开
 *
 * 阶段 5.1 / S2：`explanation` 改成可选（`?: string | null`）—— 契约里
 * `QuizItemResponse.explanation` 是 `anyOf[string, null]` 且**没有默认值**，
 * 也就是说后端既可能给 `null`、也可能整个字段不出现。渲染分支用的是
 * `{explanation && …}`，对 `undefined` 与 `null` 完全一致，所以这是纯类型修正。
 */
function AnswerSection({ answer, explanation }: { answer: string; explanation?: string | null }) {
  const [show, setShow] = useState(false);

  return (
    <div
      style={{
        borderTop: '1px solid var(--color-border)',
        paddingTop: 'var(--space-sm)',
        marginTop: 'var(--space-sm)',
      }}
    >
      <button
        className="btn btn-secondary"
        style={{
          fontSize: '0.75rem',
          padding: '2px 10px',
          marginBottom: show ? 'var(--space-sm)' : 0,
        }}
        onClick={() => setShow(!show)}
      >
        {show ? '隐藏答案' : '显示答案'}
      </button>
      {show && (
        <div>
          {/* 答案文字：原值 `#10b981` 白底只有 2.54:1（0.875rem，要求 4.5:1）——
              与 a11y-audit 的 F-36（今日学习的「低」优先级徽章，同一个色值）
              是同一次"亮绿压浅底"的洞。`#25714a` = `--color-success`，白底 5.93:1。
              这行答案此前从未被任何一层判过（要点击「显示答案」才渲染），
              本轮的 `question-sets` 场景会点开它，所以它现在有门禁。 */}
          <p style={{ fontSize: '0.875rem', color: '#25714a', lineHeight: 1.5 }}>
            <strong>答案：</strong>
            {answer}
          </p>
          {explanation && (
            <p
              style={{
                fontSize: '0.8rem',
                color: 'var(--color-text-secondary)',
                lineHeight: 1.5,
                marginTop: '4px',
              }}
            >
              <strong>解析：</strong>
              {explanation}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
