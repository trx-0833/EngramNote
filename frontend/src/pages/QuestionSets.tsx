/**
 * @file 问题集列表页面
 * @description 展示当前用户所有问答题，按所属笔记分组，每组可折叠/展开
 * 仿照 KnowledgeCards 页面结构，将每份文档的问答题集展示出来
 */
import { useEffect, useState, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getQuestions, type QuizItem } from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'

function parseOptions(optionsStr: string | null): string[] {
  if (!optionsStr) return []
  try {
    const parsed = JSON.parse(optionsStr)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}
import ErrorDisplay from '../components/ErrorDisplay'
import { questionTypeLabels, questionTypeColors, difficultyLabels, difficultyColors } from '../utils/labels'

interface NoteGroup {
  note_id: string
  note_title: string
  questions: QuizItem[]
}

export default function QuestionSets() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [groups, setGroups] = useState<NoteGroup[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set())
  const [filterType, setFilterType] = useState<string>('all')
  const [filterDifficulty, setFilterDifficulty] = useState<string>('all')
  const [searchKeyword, setSearchKeyword] = useState('')
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const noteId = searchParams.get('note_id') || undefined

  const fetchQuestions = useCallback(async (keyword?: string) => {
    setLoading(true)
    try {
      // 后端 page_size 上限为 100，需分页加载全部题目
      const allItems: QuizItem[] = []
      let page = 1
      const pageSize = 100
      let totalCount = 0
      do {
        const data = await getQuestions(page, pageSize, noteId, keyword)
        allItems.push(...data.items)
        totalCount = data.total
        page++
      } while (allItems.length < totalCount)
      const grouped = groupByNote(allItems)
      setGroups(grouped)
      setTotal(totalCount)
      setExpandedNotes(new Set(grouped.map(g => g.note_id)))
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [noteId])

  useEffect(() => {
    fetchQuestions(searchKeyword || undefined)
  }, [noteId, fetchQuestions, searchKeyword])

  function handleSearchChange(value: string) {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => {
      setSearchKeyword(value)
    }, 300)
  }

  function groupByNote(questions: QuizItem[]): NoteGroup[] {
    const map = new Map<string, QuizItem[]>()
    for (const q of questions) {
      const list = map.get(q.note_id) || []
      list.push(q)
      map.set(q.note_id, list)
    }
    return Array.from(map.entries())
      .map(([nid, qs]) => ({
        note_id: nid,
        note_title: qs[0].note_title || '未命名笔记',
        questions: qs,
      }))
      .sort((a, b) => {
        const aTime = a.questions[0]?.created_at ?? ''
        const bTime = b.questions[0]?.created_at ?? ''
        return bTime.localeCompare(aTime)
      })
  }

  function toggleGroup(noteId: string) {
    setExpandedNotes(prev => {
      const next = new Set(prev)
      if (next.has(noteId)) {
        next.delete(noteId)
      } else {
        next.add(noteId)
      }
      return next
    })
  }

  function filterQuestions(questions: QuizItem[]): QuizItem[] {
    return questions.filter(q => {
      if (filterType !== 'all' && q.question_type !== filterType) return false
      if (filterDifficulty !== 'all' && q.difficulty !== filterDifficulty) return false
      return true
    })
  }

  const totalFiltered = groups.reduce(
    (sum, g) => sum + filterQuestions(g.questions).length, 0
  )

  return (
    <div className="page-enter">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-md)', flexWrap: 'wrap', gap: 'var(--space-sm)' }}>
        <h1 className="heading-serif gradient-text" style={{ fontSize: '1.5rem' }}>问题集</h1>
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
          共 {total} 道题{filterType !== 'all' || filterDifficulty !== 'all' ? `，筛选后 ${totalFiltered} 道` : ''}
        </span>
      </div>

      {/* 搜索栏 */}
      <div style={{ marginBottom: 'var(--space-md)' }}>
        <input
          type="text"
          placeholder="搜索题目内容..."
          onChange={e => handleSearchChange(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 12px',
            border: '1px solid var(--color-border)',
            borderRadius: '8px',
            fontSize: '0.875rem',
            outline: 'none',
            background: 'var(--color-bg)',
            color: 'var(--color-text)',
            boxSizing: 'border-box',
          }}
        />
      </div>

      {/* 筛选栏 */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-lg)', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>题型：</span>
          {[
            { value: 'all', label: '全部' },
            { value: 'choice', label: '选择' },
            { value: 'fill_blank', label: '填空' },
            { value: 'short_answer', label: '简答' },
          ].map(opt => (
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
          ].map(opt => (
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
          {groups.map(group => {
            const filtered = filterQuestions(group.questions)
            if (filtered.length === 0) return null
            return (
              <div key={group.note_id} style={{ marginBottom: 'var(--space-md)' }}>
                <div
                  className="card"
                  onClick={() => toggleGroup(group.note_id)}
                  style={{
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: expandedNotes.has(group.note_id) ? 'var(--space-sm)' : 0,
                    transition: 'margin-bottom 0.15s',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
                    <span className={`collapse-arrow ${expandedNotes.has(group.note_id) ? 'collapse-arrow-open' : ''}`}>
                      ▶
                    </span>
                    <strong>{group.note_title}</strong>
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                      ({filtered.length} 道题)
                    </span>
                  </div>
                  <button
                    className="btn btn-secondary"
                    style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                    onClick={e => { e.stopPropagation(); navigate(`/notes/${group.note_id}`) }}
                  >
                    查看笔记
                  </button>
                </div>

                {expandedNotes.has(group.note_id) && (
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))',
                    gap: 'var(--space-md)',
                  }}>
                    {filtered.map(q => (
                      <div
                        key={q.id}
                        className="card card-hover"
                      >
                        {/* 题目头部：题型 + 难度标签 */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-sm)' }}>
                          <div style={{ display: 'flex', gap: '4px' }}>
                            <span
                              style={{
                                fontSize: '0.75rem',
                                padding: '2px 8px',
                                borderRadius: '9999px',
                                background: questionTypeColors[q.question_type] || '#6b7280',
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
                                background: difficultyColors[q.difficulty] || '#6b7280',
                                color: 'white',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {difficultyLabels[q.difficulty] || q.difficulty}
                            </span>
                          </div>
                        </div>

                        {/* 题目内容 */}
                        <p style={{ fontSize: '0.95rem', lineHeight: 1.6, marginBottom: 'var(--space-sm)', display: '-webkit-box', WebkitLineClamp: 4, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                          {q.question}
                        </p>

                        {/* 选择题选项 */}
                        {q.question_type === 'choice' && q.options && (
                          <div style={{ marginBottom: 'var(--space-sm)' }}>
                            {parseOptions(q.options).map((opt, i) => (
                              <div key={i} style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', padding: '2px 0', paddingLeft: 'var(--space-sm)' }}>
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
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 答案折叠组件：默认隐藏答案和解析，点击按钮展开 */
function AnswerSection({ answer, explanation }: { answer: string; explanation: string | null }) {
  const [show, setShow] = useState(false)

  return (
    <div style={{ borderTop: '1px solid var(--color-border)', paddingTop: 'var(--space-sm)', marginTop: 'var(--space-sm)' }}>
      <button
        className="btn btn-secondary"
        style={{ fontSize: '0.75rem', padding: '2px 10px', marginBottom: show ? 'var(--space-sm)' : 0 }}
        onClick={() => setShow(!show)}
      >
        {show ? '隐藏答案' : '显示答案'}
      </button>
      {show && (
        <div>
          <p style={{ fontSize: '0.875rem', color: '#10b981', lineHeight: 1.5 }}>
            <strong>答案：</strong>{answer}
          </p>
          {explanation && (
            <p style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)', lineHeight: 1.5, marginTop: '4px' }}>
              <strong>解析：</strong>{explanation}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
