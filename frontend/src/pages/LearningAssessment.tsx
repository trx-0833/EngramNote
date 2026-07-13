/**
 * @file 学习评估页面
 * @description 提供两种评估模式：
 * 1. 笔记比对：比较学习资料与个人笔记的内容覆盖度、深度和清晰度
 * 2. 开放性问题：基于学习资料生成问题，用户作答后由 AI 评判
 */
import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  getNotes,
  compareAssessment,
  generateQuiz,
  submitQuizAnswers,
  getAssessmentHistory,
  getNoteLinks,
  type AssessmentResult,
  type AssessmentHistoryItem,
  type Note,
  type NoteLinksResponse,
} from '../api/client'
import LoadingSpinner from '../components/LoadingSpinner'
import EmptyState from '../components/EmptyState'
import { renderMarkdown } from '../utils/markdown'

export default function LearningAssessment() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const preselectedNoteId = searchParams.get('noteId')

  const [mode, setMode] = useState<'compare' | 'quiz'>('compare')
  const [notes, setNotes] = useState<Note[]>([])
  const [selectedMaterials, setSelectedMaterials] = useState<string[]>([])
  const [selectedPersonalNotes, setSelectedPersonalNotes] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [notesLoading, setNotesLoading] = useState(true)
  const [notesError, setNotesError] = useState('')
  const [result, setResult] = useState<AssessmentResult | null>(null)

  // Quiz-specific state
  const [quizAssessment, setQuizAssessment] = useState<AssessmentResult | null>(null)
  const [answers, setAnswers] = useState<Record<number, string>>({})
  const [quizResult, setQuizResult] = useState<AssessmentResult | null>(null)

  // 已链接笔记模式相关 state（quiz 模式）
  const [useLinkedMode, setUseLinkedMode] = useState(false)
  const [linkablePersonalNotes, setLinkablePersonalNotes] = useState<Note[]>([])
  const [selectedPersonalNoteId, setSelectedPersonalNoteId] = useState<string | null>(null)
  const [linkedMaterials, setLinkedMaterials] = useState<Note[]>([])

  // compare 模式：已链接对比 / 手动选择（默认"已链接对比"）
  const [compareMode, setCompareMode] = useState<'linked' | 'manual'>('linked')
  const [compareLinkedPersonalNotes, setCompareLinkedPersonalNotes] = useState<Note[]>([])
  const [compareLinksLoading, setCompareLinksLoading] = useState(false)

  useEffect(() => {
    loadNotes()
  }, [])

  useEffect(() => {
    if (preselectedNoteId) {
      setSelectedMaterials([preselectedNoteId])
    }
  }, [preselectedNoteId])

  // quiz 模式 + 已链接模式：加载 personal_note 列表
  useEffect(() => {
    if (mode === 'quiz' && useLinkedMode) {
      const loadPersonalNotes = async () => {
        try {
          const data = await getNotes(1, 100, undefined, 'personal_note')
          setLinkablePersonalNotes(data.items || [])
        } catch (err) {
          console.error('加载笔记列表失败:', err)
        }
      }
      loadPersonalNotes()
    }
  }, [mode, useLinkedMode])

  // compare 模式 + 已链接对比：加载所有有 material 链接关系的 personal_note
  useEffect(() => {
    if (mode !== 'compare' || compareMode !== 'linked') return
    const loadCompareLinkedNotes = async () => {
      setCompareLinksLoading(true)
      try {
        const data = await getNotes(1, 100, undefined, 'personal_note')
        const allPersonalNotes = data.items || []
        // 并行检查每个 personal_note 是否有 material 链接
        const checked = await Promise.all(
          allPersonalNotes.map(async (n) => {
            try {
              const links = await getNoteLinks(n.id)
              return links.linked_materials.length > 0 ? n : null
            } catch {
              return null
            }
          })
        )
        setCompareLinkedPersonalNotes(checked.filter((n): n is Note => n !== null))
      } catch (err) {
        console.error('加载已链接笔记失败:', err)
        setCompareLinkedPersonalNotes([])
      } finally {
        setCompareLinksLoading(false)
      }
    }
    loadCompareLinkedNotes()
  }, [mode, compareMode])

  const loadNotes = async () => {
    try {
      // Load all notes across pages
      const data = await getNotes(1, 100)
      // Only show notes that have content available for assessment
      const assessableStatuses = ['converted', 'cleaned', 'archived', 'learning', 'learning_failed']
      setNotes(data.items.filter(n => assessableStatuses.includes(n.status)))
      setNotesError('')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载笔记失败'
      console.error('加载笔记失败:', e)
      setNotesError(msg)
    } finally {
      setNotesLoading(false)
    }
  }

  const materialNotes = notes.filter(n => n.note_role === 'material' || !n.note_role)
  const personalNotes = notes.filter(n => n.note_role === 'personal_note')

  // Compare mode handlers
  const handleCompare = async () => {
    // 已链接对比模式：使用选中的 personal_note 及其关联资料
    let materialIds = selectedMaterials
    let personalIds = selectedPersonalNotes
    if (compareMode === 'linked') {
      if (!selectedPersonalNoteId) {
        alert('请选择一个笔记')
        return
      }
      personalIds = [selectedPersonalNoteId]
    }
    if (materialIds.length === 0 || personalIds.length === 0) {
      alert('请选择学习资料和笔记')
      return
    }
    setLoading(true)
    try {
      const res = await compareAssessment(materialIds, personalIds)
      setResult(res)
    } catch (e) {
      alert('评估失败: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setLoading(false)
    }
  }

  // 切换 compare 模式子标签时清空两端选择，避免串数据
  const handleCompareModeChange = (next: 'linked' | 'manual') => {
    if (next === compareMode) return
    setCompareMode(next)
    setSelectedMaterials([])
    setSelectedPersonalNotes([])
    setSelectedPersonalNoteId(null)
    setLinkedMaterials([])
  }

  // Quiz mode handlers
  const handleGenerateQuiz = async () => {
    if (selectedMaterials.length === 0) {
      alert('请选择学习资料')
      return
    }
    setLoading(true)
    try {
      const res = await generateQuiz(selectedMaterials, useLinkedMode ? selectedPersonalNoteId || undefined : undefined)
      setQuizAssessment(res)
      setQuizResult(null)
      // Initialize empty answers
      const initialAnswers: Record<number, string> = {}
      ;(res.quiz_questions || []).forEach((_, i) => {
        initialAnswers[i] = ''
      })
      setAnswers(initialAnswers)
    } catch (e) {
      alert('生成问题失败: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setLoading(false)
    }
  }

  // 已链接模式：选中 personal_note 后加载其关联资料
  const handleSelectPersonalNote = async (noteId: string) => {
    setSelectedPersonalNoteId(noteId)
    try {
      const links: NoteLinksResponse = await getNoteLinks(noteId)
      const materialIds = links.linked_materials.map(m => m.id)
      if (materialIds.length > 0) {
        // 用 linked_materials 信息构造 Note 列表用于展示
        const linkedNotes = links.linked_materials.map(m => ({
          id: m.id,
          title: m.title,
          source_type: m.source_type || '',
        } as Note))
        setLinkedMaterials(linkedNotes)
        setSelectedMaterials(materialIds)
      } else {
        setLinkedMaterials([])
        setSelectedMaterials([])
      }
    } catch (err) {
      console.error('加载链接关系失败:', err)
    }
  }

  const handleSubmitAnswers = async () => {
    if (!quizAssessment) return
    // Check all answers are filled
    const unanswered = Object.values(answers).some(a => !a.trim())
    if (unanswered) {
      alert('请回答所有问题')
      return
    }
    setLoading(true)
    try {
      const answerList = Object.entries(answers).map(([idx, answer]) => ({
        question_index: parseInt(idx),
        answer,
      }))
      const res = await submitQuizAnswers(quizAssessment.id, answerList)
      setQuizResult(res)
    } catch (e) {
      alert('提交答案失败: ' + (e instanceof Error ? e.message : String(e)))
    } finally {
      setLoading(false)
    }
  }

  // Render score bar
  const renderScoreBar = (label: string, score: number) => {
    const fillClass = score >= 80 ? 'score-bar-fill-high' : score >= 60 ? 'score-bar-fill-mid' : 'score-bar-fill-low'
    return (
      <div className="score-bar">
        <div className="score-bar-header">
          <span className="score-bar-label">{label}</span>
          <span className="score-bar-value">{score}<span style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary)', fontWeight: 400 }}>/100</span></span>
        </div>
        <div className="score-bar-track">
          <div className={`score-bar-fill ${fillClass}`} style={{ width: `${score}%` }} />
        </div>
      </div>
    )
  }

  // Render note selection card
  const renderNoteCard = (note: Note, selected: boolean, onToggle: (checked: boolean) => void) => (
    <label className={`note-select-card ${selected ? 'note-select-card-checked' : ''}`}>
      <input
        type="checkbox"
        checked={selected}
        onChange={e => onToggle(e.target.checked)}
      />
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{note.title}</span>
      {note.source_type && (
        <span
          className={`badge badge-${note.source_type}`}
          style={{ fontSize: '0.7rem', padding: '1px 6px', flexShrink: 0 }}
        >
          {note.source_type}
        </span>
      )}
    </label>
  )

  return (
    <div className="page-enter" style={{ maxWidth: '960px', margin: '0 auto' }}>
      {/* Header */}
      <div className="assessment-header">
        <h1 className="assessment-title">学习评估</h1>
        <p className="assessment-subtitle">通过笔记比对或开放性问题，评估你对学习资料的掌握程度</p>
      </div>

      {/* Mode selection */}
      <div className="segment-control" style={{ marginBottom: 'var(--space-lg)' }}>
        <button
          className={`segment-btn ${mode === 'compare' ? 'segment-btn-active' : ''}`}
          onClick={() => { setMode('compare'); setResult(null) }}
        >
          笔记比对
        </button>
        <button
          className={`segment-btn ${mode === 'quiz' ? 'segment-btn-active' : ''}`}
          onClick={() => { setMode('quiz'); setQuizAssessment(null); setQuizResult(null) }}
        >
          开放性问题
        </button>
      </div>

      {notesLoading ? (
        <LoadingSpinner />
      ) : notesError ? (
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <p style={{ color: 'var(--color-error)' }}>加载笔记失败: {notesError}</p>
          <button className="btn btn-secondary" style={{ marginTop: 'var(--space-sm)' }} onClick={loadNotes}>重试</button>
        </div>
      ) : mode === 'compare' ? (
        <>
          {/* compare 模式子标签：已链接对比 / 手动选择 */}
          <div className="segment-control" style={{ marginBottom: 'var(--space-lg)' }}>
            <button
              className={`segment-btn ${compareMode === 'linked' ? 'segment-btn-active' : ''}`}
              onClick={() => handleCompareModeChange('linked')}
            >
              已链接对比
            </button>
            <button
              className={`segment-btn ${compareMode === 'manual' ? 'segment-btn-active' : ''}`}
              onClick={() => handleCompareModeChange('manual')}
            >
              手动选择
            </button>
          </div>

          {compareMode === 'linked' ? (
            /* 已链接对比模式：列出有 material 链接的 personal_note */
            compareLinksLoading ? (
              <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
                <p style={{ color: 'var(--color-text-secondary)' }}>加载已链接笔记...</p>
              </div>
            ) : compareLinkedPersonalNotes.length === 0 ? (
              /* 空状态：无任何有链接关系的 personal_note */
              <EmptyState
                message="暂无已链接的笔记"
                description="请先在笔记详情页关联资料后再使用此功能"
                action={<button className="btn btn-primary" onClick={() => navigate('/notes')}>前往笔记列表</button>}
              />
            ) : (
              <div style={{ marginBottom: 'var(--space-lg)' }}>
                <h3 style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-sm)' }}>选择笔记</h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 'var(--space-sm)' }}>
                  {compareLinkedPersonalNotes.map(n => (
                    <div
                      key={n.id}
                      className={`note-select-card ${selectedPersonalNoteId === n.id ? 'note-select-card-checked' : ''}`}
                      onClick={() => handleSelectPersonalNote(n.id)}
                    >
                      <h4 style={{ marginBottom: '0.25rem' }}>{n.title}</h4>
                      <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                        关联资料: {selectedPersonalNoteId === n.id ? linkedMaterials.length : '—'} 篇
                      </p>
                    </div>
                  ))}
                </div>
                {selectedPersonalNoteId && linkedMaterials.length > 0 && (
                  <div style={{ marginTop: 'var(--space-md)' }}>
                    <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>将比对以下资料与该笔记：</p>
                    {linkedMaterials.map(m => (
                      <div key={m.id} style={{ padding: '0.25rem 0' }}>• {m.title}</div>
                    ))}
                  </div>
                )}
              </div>
            )
          ) : (
            /* 手动选择模式（保留现有独立选择逻辑，向后兼容）*/
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-lg)', marginBottom: 'var(--space-lg)' }}>
              {/* Material notes */}
              <div className="card">
                <h3 style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-sm)' }}>学习资料</h3>
                {materialNotes.length === 0 ? (
                  <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>暂无学习资料</p>
                ) : (
                  materialNotes.map(note => renderNoteCard(note, selectedMaterials.includes(note.id), (checked) => {
                    setSelectedMaterials(prev => checked ? [...prev, note.id] : prev.filter(id => id !== note.id))
                  }))
                )}
              </div>
              {/* Personal notes */}
              <div className="card">
                <h3 style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-sm)' }}>我的笔记</h3>
                {personalNotes.length === 0 ? (
                  <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>暂无个人笔记（请先上传并标记为"我的笔记"）</p>
                ) : (
                  personalNotes.map(note => renderNoteCard(note, selectedPersonalNotes.includes(note.id), (checked) => {
                    setSelectedPersonalNotes(prev => checked ? [...prev, note.id] : prev.filter(id => id !== note.id))
                  }))
                )}
              </div>
            </div>
          )}

          {/* 开始评估按钮：手动模式始终显示；已链接模式仅在非空状态时显示 */}
          {(compareMode === 'manual' || (!compareLinksLoading && compareLinkedPersonalNotes.length > 0)) && (
            <button className="btn btn-primary" onClick={handleCompare} disabled={loading} style={{ marginBottom: 'var(--space-lg)' }}>
              {loading ? '评估中...' : '开始评估'}
            </button>
          )}

          {/* Results */}
          {result && (
            <div className="card" style={{ animation: 'slideUp 0.4s var(--ease-out-expo)' }}>
              <h3 className="heading-serif" style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}>评估结果</h3>
              {renderScoreBar('内容覆盖度', result.scores?.coverage_score || 0)}
              {renderScoreBar('思考深度', result.scores?.depth_score || 0)}
              {renderScoreBar('结构清晰度', result.scores?.clarity_score || 0)}
              {renderScoreBar('综合评分', result.overall_score)}

              {(result.scores?.covered_points?.length > 0 || result.scores?.uncovered_points?.length > 0) && (
                <div className="knowledge-points-grid">
                  {result.scores?.covered_points?.length > 0 && (
                    <div className="knowledge-points-section">
                      <h4 style={{ color: 'var(--color-success)' }}>
                        <span>✓</span> 已覆盖知识点
                      </h4>
                      <ul>
                        {result.scores.covered_points.map((p: string, i: number) => (
                          <li key={i}><div dangerouslySetInnerHTML={{ __html: renderMarkdown(p) }} /></li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {result.scores?.uncovered_points?.length > 0 && (
                    <div className="knowledge-points-section">
                      <h4 style={{ color: 'var(--color-error)' }}>
                        <span>✗</span> 未覆盖知识点
                      </h4>
                      <ul>
                        {result.scores.uncovered_points.map((p: string, i: number) => (
                          <li key={i}><div dangerouslySetInnerHTML={{ __html: renderMarkdown(p) }} /></li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
              {result.suggestions && (
                <div style={{ marginTop: 'var(--space-md)', padding: 'var(--space-sm) var(--space-md)', background: 'var(--color-primary-light)', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', borderLeft: '3px solid var(--color-primary)' }}>
                  <strong>改进建议：</strong>
                  <div dangerouslySetInnerHTML={{ __html: renderMarkdown(result.suggestions) }} />
                </div>
              )}
            </div>
          )}
        </>
      ) : (
        <>
          {/* Quiz mode - 使用已链接的笔记切换 */}
          <div style={{ marginBottom: 'var(--space-md)' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={useLinkedMode}
                onChange={(e) => {
                  setUseLinkedMode(e.target.checked)
                  setSelectedPersonalNoteId(null)
                  setLinkedMaterials([])
                  setSelectedMaterials([])
                }}
              />
              <span>使用已链接的笔记</span>
            </label>
          </div>

          {/* Quiz mode - 已链接模式：选择 personal_note */}
          {mode === 'quiz' && useLinkedMode ? (
            <div style={{ marginBottom: 'var(--space-lg)' }}>
              <h3 style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-sm)' }}>选择笔记</h3>
              {linkablePersonalNotes.length === 0 ? (
                <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>暂无可选笔记</p>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 'var(--space-sm)' }}>
                  {linkablePersonalNotes.map(n => (
                    <div
                      key={n.id}
                      className={`note-select-card ${selectedPersonalNoteId === n.id ? 'note-select-card-checked' : ''}`}
                      onClick={() => handleSelectPersonalNote(n.id)}
                    >
                      <h4 style={{ marginBottom: '0.25rem' }}>{n.title}</h4>
                      <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                        关联资料: {linkedMaterials.length} 篇
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {selectedPersonalNoteId && linkedMaterials.length > 0 && (
                <div style={{ marginTop: 'var(--space-md)' }}>
                  <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>将基于以下资料生成开放性问题：</p>
                  {linkedMaterials.map(m => (
                    <div key={m.id} style={{ padding: '0.25rem 0' }}>• {m.title}</div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* Quiz mode - 手动选择资料 */
            <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
              <h3 style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-sm)' }}>学习资料</h3>
              {materialNotes.length === 0 ? (
                <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>暂无学习资料</p>
              ) : (
                materialNotes.map(note => renderNoteCard(note, selectedMaterials.includes(note.id), (checked) => {
                  setSelectedMaterials(prev => checked ? [...prev, note.id] : prev.filter(id => id !== note.id))
                }))
              )}
            </div>
          )}

          {!quizAssessment ? (
            <button className="btn btn-primary" onClick={handleGenerateQuiz} disabled={loading}>
              {loading ? '生成中...' : '生成问题'}
            </button>
          ) : (
            <>
              {/* Questions */}
              {(quizAssessment.quiz_questions || []).map((q, idx) => (
                <div key={idx} className="quiz-question-card">
                  <div className="quiz-question-text">
                    <span className="quiz-question-number">{idx + 1}</span>
                    <div style={{ flex: 1 }} dangerouslySetInnerHTML={{ __html: renderMarkdown(q.question) }} />
                  </div>
                  <textarea
                    value={answers[idx] || ''}
                    onChange={e => setAnswers(prev => ({ ...prev, [idx]: e.target.value }))}
                    placeholder="请输入你的答案..."
                    style={{ width: '100%', minHeight: '100px' }}
                  />
                </div>
              ))}

              {!quizResult ? (
                <button className="btn btn-primary" onClick={handleSubmitAnswers} disabled={loading}>
                  {loading ? '评判中...' : '提交答案'}
                </button>
              ) : (
                <>
                  {/* Quiz judgment results */}
                  {(quizResult.quiz_answers || []).map((qa: any, idx: number) => (
                    <div key={idx} className="quiz-question-card">
                      {/* 题目 */}
                      <div className="quiz-question-text">
                        <span className="quiz-question-number">{idx + 1}</span>
                        <div style={{ flex: 1 }} dangerouslySetInnerHTML={{ __html: renderMarkdown(quizAssessment?.quiz_questions?.[idx]?.question || '') }} />
                      </div>
                      {/* 用户答案回显 */}
                      {qa.answer && (
                        <div style={{ marginTop: 'var(--space-sm)', padding: 'var(--space-sm) var(--space-md)', fontSize: '0.875rem', borderLeft: '3px solid var(--color-text-tertiary)', color: 'var(--color-text-secondary)' }}>
                          <strong style={{ color: 'var(--color-text-primary)' }}>你的回答：</strong>
                          <div dangerouslySetInnerHTML={{ __html: renderMarkdown(qa.answer) }} />
                        </div>
                      )}
                      {/* 评判结果 */}
                      <div style={{ marginTop: 'var(--space-sm)', fontSize: '0.875rem', fontWeight: 600, color: 'var(--color-text-secondary)' }}>评判结果</div>
                      {renderScoreBar('准确性', qa.judgment?.accuracy_score || 0)}
                      {renderScoreBar('完整性', qa.judgment?.completeness_score || 0)}
                      {renderScoreBar('深度', qa.judgment?.depth_score || 0)}
                      {qa.judgment?.feedback && (
                        <div style={{ marginTop: 'var(--space-sm)', padding: 'var(--space-sm) var(--space-md)', background: 'var(--color-accent-light)', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', borderLeft: '3px solid var(--color-accent)' }}>
                          {qa.judgment.feedback}
                        </div>
                      )}
                    </div>
                  ))}
                  <div className="score-summary-card">
                    <div className="score-summary-number">{quizResult.overall_score}</div>
                    <div className="score-summary-label">综合评分</div>
                    {quizResult.suggestions && (
                      <div style={{ fontSize: '0.875rem', marginTop: 'var(--space-md)', color: 'var(--color-text-secondary)', textAlign: 'left' }} dangerouslySetInnerHTML={{ __html: renderMarkdown(quizResult.suggestions) }} />
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
