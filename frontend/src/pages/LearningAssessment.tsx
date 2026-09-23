/**
 * @file 学习评估页面
 * @description 提供两种评估模式：
 * 1. 笔记比对：比较学习资料与个人笔记的内容覆盖度、深度和清晰度
 * 2. 开放性问题：基于学习资料生成问题，用户作答后由 AI 评判
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  getNotes,
  compareAssessment,
  generateQuiz,
  submitQuizAnswers,
  getNoteLinks,
  type AssessmentResult,
  type QuizAnswerItem,
  type Note,
  type NoteLinksResponse,
} from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
// 页面标题（visual-refactor-plan 批次 C1）：原先借全局 `.assessment-title`
// （1.75rem + 渐变字），现在统一成 <PageHeader> 的 1.5rem —— 见下方调用点
import PageHeader from '../components/PageHeader';
import { renderMarkdown } from '../utils/markdown';
import { useToast } from '../components/Toast';
// 本页私有样式（overhaul-plan 5.6 序 5）：`.score-bar*` / `.quiz-question-*` /
// `.score-summary-*` / `.knowledge-points-*` 从 `src/styles/assessment.css` 拆出，
// 与 `refinements.css` 打架的那 14 条按实测胜者并入，480px 的
// `.knowledge-points-grid` 从 `responsive.css` 一起搬进来 —— 见 LearningAssessment.module.css 文件头
import styles from './LearningAssessment.module.css';

/**
 * 评分条填充色的类名查表。
 *
 * ⚠️ **不能**写成模板串 `` `score-bar-fill-${level}` ``：类名进 CSS Modules 后
 * 会被哈希（`.scoreBarFillHigh` → `._scoreBarFillHigh_hash`），拼出来的字符串
 * 在产物里不存在 —— 构建期不报错、规则清单也看不出来，只是颜色静默消失。
 * 查表还能让 `tsc` 守住完整性（少一个键直接报错）。同 `DiffView.tsx` 的 `LINE_TYPE_CLASS`。
 */
const SCORE_FILL_CLASS: Record<'high' | 'mid' | 'low', string> = {
  high: styles.scoreBarFillHigh,
  mid: styles.scoreBarFillMid,
  low: styles.scoreBarFillLow,
};

/**
 * 「选择笔记」卡片里那个按钮的外观复位。
 *
 * 卡片（`.note-select-card`）的样式没动，动的是**谁接行为**：原来是整个
 * `div[onClick]`（键盘到不了），现在是标题里一个真 `<button>`。
 * `<button>` 自带 UA 样式（系统字体、灰底、2px 边框、居中文字、内边距），
 * 不复位就是一次改版；下面的取值逐项对应改动前那一行标题的实际外观
 * （字号 1rem 与字重都从外层 `h3` 继承）。
 */
const noteSelectButtonStyle: React.CSSProperties = {
  margin: 0,
  padding: 0,
  border: 'none',
  background: 'none',
  font: 'inherit',
  color: 'inherit',
  textAlign: 'left',
  cursor: 'pointer',
};

export default function LearningAssessment() {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const preselectedNoteId = searchParams.get('noteId');

  const [mode, setMode] = useState<'compare' | 'quiz'>('compare');
  const [notes, setNotes] = useState<Note[]>([]);
  const [selectedMaterials, setSelectedMaterials] = useState<string[]>([]);
  const [selectedPersonalNotes, setSelectedPersonalNotes] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  /** 提交 in-flight 锁（防双击重复提交），见 docs/decisions.md#F-23 */
  const submittingRef = useRef(false);
  const [notesLoading, setNotesLoading] = useState(true);
  const [notesError, setNotesError] = useState('');
  const [result, setResult] = useState<AssessmentResult | null>(null);

  // Quiz-specific state
  const [quizAssessment, setQuizAssessment] = useState<AssessmentResult | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [quizResult, setQuizResult] = useState<AssessmentResult | null>(null);

  // 已链接笔记模式相关 state（quiz 模式）
  const [useLinkedMode, setUseLinkedMode] = useState(false);
  const [linkablePersonalNotes, setLinkablePersonalNotes] = useState<Note[]>([]);
  const [selectedPersonalNoteId, setSelectedPersonalNoteId] = useState<string | null>(null);
  const [linkedMaterials, setLinkedMaterials] = useState<Note[]>([]);

  // compare 模式：已链接对比 / 手动选择（默认"已链接对比"）
  const [compareMode, setCompareMode] = useState<'linked' | 'manual'>('linked');
  const [compareLinkedPersonalNotes, setCompareLinkedPersonalNotes] = useState<Note[]>([]);
  const [compareLinksLoading, setCompareLinksLoading] = useState(false);

  const loadNotes = useCallback(async () => {
    try {
      // Load all notes across pages
      const data = await getNotes(1, 100);
      // Only show notes that have content available for assessment
      const assessableStatuses = [
        'converted',
        'cleaned',
        'archived',
        'learning',
        'learning_failed',
      ];
      setNotes(data.items.filter((n) => assessableStatuses.includes(n.status)));
      setNotesError('');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载笔记失败';
      console.error('加载笔记失败:', e);
      setNotesError(msg);
    } finally {
      setNotesLoading(false);
    }
  }, []);

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadNotes();
  }, [loadNotes]);

  useEffect(() => {
    if (preselectedNoteId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedMaterials([preselectedNoteId]);
    }
  }, [preselectedNoteId]);

  // quiz 模式 + 已链接模式：加载 personal_note 列表
  useEffect(() => {
    if (mode === 'quiz' && useLinkedMode) {
      const loadPersonalNotes = async () => {
        try {
          const data = await getNotes(1, 100, undefined, 'personal_note');
          setLinkablePersonalNotes(data.items || []);
        } catch (err) {
          console.error('加载笔记列表失败:', err);
        }
      };
      loadPersonalNotes();
    }
  }, [mode, useLinkedMode]);

  // compare 模式 + 已链接对比：加载所有有 material 链接关系的 personal_note
  useEffect(() => {
    if (mode !== 'compare' || compareMode !== 'linked') return;
    const loadCompareLinkedNotes = async () => {
      setCompareLinksLoading(true);
      try {
        const data = await getNotes(1, 100, undefined, 'personal_note');
        const allPersonalNotes = data.items || [];
        // 并行检查每个 personal_note 是否有 material 链接
        const checked = await Promise.all(
          allPersonalNotes.map(async (n) => {
            try {
              const links = await getNoteLinks(n.id);
              return links.linked_materials.length > 0 ? n : null;
            } catch {
              return null;
            }
          }),
        );
        setCompareLinkedPersonalNotes(checked.filter((n): n is Note => n !== null));
      } catch (err) {
        console.error('加载已链接笔记失败:', err);
        setCompareLinkedPersonalNotes([]);
      } finally {
        setCompareLinksLoading(false);
      }
    };
    loadCompareLinkedNotes();
  }, [mode, compareMode]);

  const materialNotes = notes.filter((n) => n.note_role === 'material' || !n.note_role);
  const personalNotes = notes.filter((n) => n.note_role === 'personal_note');

  // Compare mode handlers
  const handleCompare = async () => {
    // 已链接对比模式：使用选中的 personal_note 及其关联资料
    const materialIds = selectedMaterials;
    let personalIds = selectedPersonalNotes;
    if (compareMode === 'linked') {
      if (!selectedPersonalNoteId) {
        toast.warning('请选择一个笔记');
        return;
      }
      personalIds = [selectedPersonalNoteId];
    }
    if (materialIds.length === 0 || personalIds.length === 0) {
      toast.warning('请选择学习资料和笔记');
      return;
    }
    setLoading(true);
    try {
      const res = await compareAssessment(materialIds, personalIds);
      setResult(res);
    } catch (e) {
      toast.error('评估失败: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  // 切换 compare 模式子标签时清空两端选择，避免串数据
  const handleCompareModeChange = (next: 'linked' | 'manual') => {
    if (next === compareMode) return;
    setCompareMode(next);
    setSelectedMaterials([]);
    setSelectedPersonalNotes([]);
    setSelectedPersonalNoteId(null);
    setLinkedMaterials([]);
  };

  // Quiz mode handlers
  const handleGenerateQuiz = async () => {
    if (selectedMaterials.length === 0) {
      toast.warning('请选择学习资料');
      return;
    }
    setLoading(true);
    try {
      const res = await generateQuiz(
        selectedMaterials,
        useLinkedMode ? selectedPersonalNoteId || undefined : undefined,
      );
      setQuizAssessment(res);
      setQuizResult(null);
      // Initialize empty answers
      const initialAnswers: Record<number, string> = {};
      (res.quiz_questions || []).forEach((_, i) => {
        initialAnswers[i] = '';
      });
      setAnswers(initialAnswers);
    } catch (e) {
      toast.error('生成问题失败: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  };

  // 已链接模式：选中 personal_note 后加载其关联资料
  const handleSelectPersonalNote = async (noteId: string) => {
    setSelectedPersonalNoteId(noteId);
    try {
      const links: NoteLinksResponse = await getNoteLinks(noteId);
      const materialIds = links.linked_materials.map((m) => m.id);
      if (materialIds.length > 0) {
        // 用 linked_materials 信息构造 Note 列表用于展示
        const linkedNotes = links.linked_materials.map(
          (m) =>
            ({
              id: m.id,
              title: m.title,
              source_type: m.source_type || '',
            }) as Note,
        );
        setLinkedMaterials(linkedNotes);
        setSelectedMaterials(materialIds);
      } else {
        setLinkedMaterials([]);
        setSelectedMaterials([]);
      }
    } catch (err) {
      console.error('加载链接关系失败:', err);
    }
  };

  const handleSubmitAnswers = async () => {
    // in-flight 锁，防止双击重复提交（重复消费 AI 评判），见 docs/decisions.md#F-23
    if (submittingRef.current) return;
    if (!quizAssessment) return;
    // Check all answers are filled
    const unanswered = Object.values(answers).some((a) => !a.trim());
    if (unanswered) {
      toast.error('请回答所有问题');
      return;
    }
    submittingRef.current = true;
    setLoading(true);
    try {
      const answerList = Object.entries(answers).map(([idx, answer]) => ({
        question_index: parseInt(idx),
        answer,
      }));
      const res = await submitQuizAnswers(quizAssessment.id, answerList);
      setQuizResult(res);
    } catch (e) {
      toast.error('提交答案失败: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
      submittingRef.current = false;
    }
  };

  // Render score bar
  const renderScoreBar = (label: string, score: number) => {
    const fillClass =
      score >= 80
        ? SCORE_FILL_CLASS.high
        : score >= 60
          ? SCORE_FILL_CLASS.mid
          : SCORE_FILL_CLASS.low;
    return (
      <div className={styles.scoreBar}>
        <div className={styles.scoreBarHeader}>
          <span className={styles.scoreBarLabel}>{label}</span>
          <span className={styles.scoreBarValue}>
            {score}
            <span
              style={{ fontSize: '0.75rem', color: 'var(--color-text-tertiary)', fontWeight: 400 }}
            >
              /100
            </span>
          </span>
        </div>
        <div className={styles.scoreBarTrack}>
          <div className={`${styles.scoreBarFill} ${fillClass}`} style={{ width: `${score}%` }} />
        </div>
      </div>
    );
  };

  // Render note selection card
  const renderNoteCard = (note: Note, selected: boolean, onToggle: (checked: boolean) => void) => (
    <label className={`note-select-card ${selected ? 'note-select-card-checked' : ''}`}>
      <input type="checkbox" checked={selected} onChange={(e) => onToggle(e.target.checked)} />
      <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {note.title}
      </span>
      {note.source_type && (
        <span
          className={`badge badge-${note.source_type}`}
          style={{ fontSize: '0.7rem', padding: '1px 6px', flexShrink: 0 }}
        >
          {note.source_type}
        </span>
      )}
    </label>
  );

  return (
    <div className="page-enter" style={{ maxWidth: '960px', margin: '0 auto' }}>
      {/* 页头（批次 C1）：原来这一块靠 `src/styles/assessment.css` 的
          `.assessment-header` / `.assessment-title` / `.assessment-subtitle`
          三个**全局**类名（回看当时的判据：那两个页面都在用同一组类名，
          所以不能塞进任一页面的模块 —— 不是"借了别家模块的类名"）。
          整块交给 `<PageHeader>` 之后那三个全局类名一起退休，见 assessment.css。
          影响面：1.75rem → 1.5rem（变小，有意为之）；
          副标题 0.9rem → 0.875rem（`--text-base`，差 0.4px，肉眼不可分）；
          `spacing="xl"` 保持原来 `.assessment-header` 的 `margin-bottom: --space-xl`。 */}
      <PageHeader
        title="学习评估"
        subtitle="通过笔记比对或开放性问题，评估你对学习资料的掌握程度"
        spacing="xl"
      />

      {/* Mode selection */}
      <div className="segment-control" style={{ marginBottom: 'var(--space-lg)' }}>
        <button
          className={`segment-btn ${mode === 'compare' ? 'segment-btn-active' : ''}`}
          onClick={() => {
            setMode('compare');
            setResult(null);
          }}
        >
          笔记比对
        </button>
        <button
          className={`segment-btn ${mode === 'quiz' ? 'segment-btn-active' : ''}`}
          onClick={() => {
            setMode('quiz');
            setQuizAssessment(null);
            setQuizResult(null);
          }}
        >
          开放性问题
        </button>
      </div>

      {notesLoading ? (
        <LoadingSpinner />
      ) : notesError ? (
        <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
          <p style={{ color: 'var(--color-error)' }}>加载笔记失败: {notesError}</p>
          <button
            className="btn btn-secondary"
            style={{ marginTop: 'var(--space-sm)' }}
            onClick={loadNotes}
          >
            重试
          </button>
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
                action={
                  <button className="btn btn-primary" onClick={() => navigate('/notes')}>
                    前往笔记列表
                  </button>
                }
              />
            ) : (
              <div style={{ marginBottom: 'var(--space-lg)' }}>
                {/* `h2` 而不是 `h3`：这一页的页面标题是 h1（学习评估），
                    这些是它的**直接下级区块**（选择笔记 / 学习资料 / 我的笔记 /
                    评估结果），中间不存在第三级 —— 写成 h3 就是 h1 → h3 跳级
                    （axe 的 heading-order）。字号/字重/字距都显式钉着，
                    所以**一个像素都没动**，做法与已修的 F-18（笔记列表卡片
                    h3→h2）、F-14/F-15（CardFace h3→h2）逐字相同。 */}
                <h2
                  style={{
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    color: 'var(--color-text-tertiary)',
                    marginBottom: 'var(--space-sm)',
                  }}
                >
                  选择笔记
                </h2>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
                    gap: 'var(--space-sm)',
                  }}
                >
                  {compareLinkedPersonalNotes.map((n) => (
                    /* ⚠️ 原来卡片是 `div[onClick]`（没有 role/tabIndex）—— 键盘**到不了**，
                       而"选中这张笔记"是整页下一步（比对/出题）的前提。
                       形状与本文件里已有的多选卡片（`renderNoteCard`：
                       `<label>` + 真 `<input type="checkbox">`）一致：**卡片是盒子，
                       控件在标题上**。选中的是单张笔记，`aria-pressed` 表达选中态 ——
                       原来只有 `.note-select-card-checked` 这个视觉类名，读屏读不到。
                       字号 1rem 与 h3 继承来的 bold 都显式保留，**视觉不变**。 */
                    <div
                      key={n.id}
                      className={`note-select-card ${selectedPersonalNoteId === n.id ? 'note-select-card-checked' : ''}`}
                    >
                      <h3 style={{ marginBottom: '0.25rem', fontSize: '1rem' }}>
                        <button
                          type="button"
                          onClick={() => handleSelectPersonalNote(n.id)}
                          aria-pressed={selectedPersonalNoteId === n.id}
                          style={noteSelectButtonStyle}
                        >
                          {n.title}
                        </button>
                      </h3>
                      <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                        关联资料: {selectedPersonalNoteId === n.id ? linkedMaterials.length : '—'}{' '}
                        篇
                      </p>
                    </div>
                  ))}
                </div>
                {selectedPersonalNoteId && linkedMaterials.length > 0 && (
                  <div style={{ marginTop: 'var(--space-md)' }}>
                    <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>
                      将比对以下资料与该笔记：
                    </p>
                    {linkedMaterials.map((m) => (
                      <div key={m.id} style={{ padding: '0.25rem 0' }}>
                        • {m.title}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          ) : (
            /* 手动选择模式（保留现有独立选择逻辑，向后兼容）*/
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 'var(--space-lg)',
                marginBottom: 'var(--space-lg)',
              }}
            >
              {/* Material notes */}
              <div className="card">
                <h2
                  style={{
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    color: 'var(--color-text-tertiary)',
                    marginBottom: 'var(--space-sm)',
                  }}
                >
                  学习资料
                </h2>
                {materialNotes.length === 0 ? (
                  <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>
                    暂无学习资料
                  </p>
                ) : (
                  materialNotes.map((note) =>
                    renderNoteCard(note, selectedMaterials.includes(note.id), (checked) => {
                      setSelectedMaterials((prev) =>
                        checked ? [...prev, note.id] : prev.filter((id) => id !== note.id),
                      );
                    }),
                  )
                )}
              </div>
              {/* Personal notes */}
              <div className="card">
                <h2
                  style={{
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    color: 'var(--color-text-tertiary)',
                    marginBottom: 'var(--space-sm)',
                  }}
                >
                  我的笔记
                </h2>
                {personalNotes.length === 0 ? (
                  <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>
                    暂无个人笔记（请先上传并标记为"我的笔记"）
                  </p>
                ) : (
                  personalNotes.map((note) =>
                    renderNoteCard(note, selectedPersonalNotes.includes(note.id), (checked) => {
                      setSelectedPersonalNotes((prev) =>
                        checked ? [...prev, note.id] : prev.filter((id) => id !== note.id),
                      );
                    }),
                  )
                )}
              </div>
            </div>
          )}

          {/* 开始评估按钮：手动模式始终显示；已链接模式仅在非空状态时显示 */}
          {(compareMode === 'manual' ||
            (!compareLinksLoading && compareLinkedPersonalNotes.length > 0)) && (
            <button
              className="btn btn-primary"
              onClick={handleCompare}
              disabled={loading}
              style={{ marginBottom: 'var(--space-lg)' }}
            >
              {loading ? '评估中...' : '开始评估'}
            </button>
          )}

          {/* Results */}
          {result && (
            <div className="card" style={{ animation: 'slideUp 0.4s var(--ease-out-expo)' }}>
              <h2
                className="heading-serif"
                style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: 'var(--space-md)' }}
              >
                评估结果
              </h2>
              {renderScoreBar('内容覆盖度', result.scores?.coverage_score || 0)}
              {renderScoreBar('思考深度', result.scores?.depth_score || 0)}
              {renderScoreBar('结构清晰度', result.scores?.clarity_score || 0)}
              {renderScoreBar('综合评分', result.overall_score)}

              {((result.scores?.covered_points?.length ?? 0) > 0 ||
                (result.scores?.uncovered_points?.length ?? 0) > 0) && (
                <div className={styles.knowledgePointsGrid}>
                  {(result.scores?.covered_points?.length ?? 0) > 0 && (
                    <div className={styles.knowledgePointsSection}>
                      <h3 style={{ color: 'var(--color-success)', fontSize: '1rem' }}>
                        <span>✓</span> 已覆盖知识点
                      </h3>
                      <ul>
                        {result.scores?.covered_points?.map((p: string, i: number) => (
                          <li key={i}>
                            <div dangerouslySetInnerHTML={{ __html: renderMarkdown(p) }} />
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {(result.scores?.uncovered_points?.length ?? 0) > 0 && (
                    <div className={styles.knowledgePointsSection}>
                      <h3 style={{ color: 'var(--color-error)', fontSize: '1rem' }}>
                        <span>✗</span> 未覆盖知识点
                      </h3>
                      <ul>
                        {result.scores?.uncovered_points?.map((p: string, i: number) => (
                          <li key={i}>
                            <div dangerouslySetInnerHTML={{ __html: renderMarkdown(p) }} />
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
              {result.suggestions && (
                <div
                  style={{
                    marginTop: 'var(--space-md)',
                    padding: 'var(--space-sm) var(--space-md)',
                    background: 'var(--color-primary-light)',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.875rem',
                    borderLeft: '3px solid var(--color-primary)',
                  }}
                >
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
            <label
              style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}
            >
              <input
                type="checkbox"
                checked={useLinkedMode}
                onChange={(e) => {
                  setUseLinkedMode(e.target.checked);
                  setSelectedPersonalNoteId(null);
                  setLinkedMaterials([]);
                  setSelectedMaterials([]);
                }}
              />
              <span>使用已链接的笔记</span>
            </label>
          </div>

          {/* Quiz mode - 已链接模式：选择 personal_note */}
          {mode === 'quiz' && useLinkedMode ? (
            <div style={{ marginBottom: 'var(--space-lg)' }}>
              <h2
                style={{
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: 'var(--space-sm)',
                }}
              >
                选择笔记
              </h2>
              {linkablePersonalNotes.length === 0 ? (
                <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>
                  暂无可选笔记
                </p>
              ) : (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
                    gap: 'var(--space-sm)',
                  }}
                >
                  {linkablePersonalNotes.map((n) => (
                    /* 与上面 compare 模式那一份同形（同一处修复的第二条渲染路径） */
                    <div
                      key={n.id}
                      className={`note-select-card ${selectedPersonalNoteId === n.id ? 'note-select-card-checked' : ''}`}
                    >
                      <h3 style={{ marginBottom: '0.25rem', fontSize: '1rem' }}>
                        <button
                          type="button"
                          onClick={() => handleSelectPersonalNote(n.id)}
                          aria-pressed={selectedPersonalNoteId === n.id}
                          style={noteSelectButtonStyle}
                        >
                          {n.title}
                        </button>
                      </h3>
                      <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
                        关联资料: {linkedMaterials.length} 篇
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {selectedPersonalNoteId && linkedMaterials.length > 0 && (
                <div style={{ marginTop: 'var(--space-md)' }}>
                  <p style={{ fontWeight: 500, marginBottom: 'var(--space-sm)' }}>
                    将基于以下资料生成开放性问题：
                  </p>
                  {linkedMaterials.map((m) => (
                    <div key={m.id} style={{ padding: '0.25rem 0' }}>
                      • {m.title}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* Quiz mode - 手动选择资料 */
            <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
              <h2
                style={{
                  fontSize: '0.8rem',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  color: 'var(--color-text-tertiary)',
                  marginBottom: 'var(--space-sm)',
                }}
              >
                学习资料
              </h2>
              {materialNotes.length === 0 ? (
                <p style={{ color: 'var(--color-text-tertiary)', fontSize: '0.875rem' }}>
                  暂无学习资料
                </p>
              ) : (
                materialNotes.map((note) =>
                  renderNoteCard(note, selectedMaterials.includes(note.id), (checked) => {
                    setSelectedMaterials((prev) =>
                      checked ? [...prev, note.id] : prev.filter((id) => id !== note.id),
                    );
                  }),
                )
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
                <div key={idx} className={styles.quizQuestionCard}>
                  <div className={styles.quizQuestionText}>
                    <span className={styles.quizQuestionNumber}>{idx + 1}</span>
                    <div
                      style={{ flex: 1 }}
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(q.question) }}
                    />
                  </div>
                  <textarea
                    value={answers[idx] || ''}
                    onChange={(e) => setAnswers((prev) => ({ ...prev, [idx]: e.target.value }))}
                    placeholder="请输入你的答案..."
                    style={{ width: '100%', minHeight: '100px' }}
                  />
                </div>
              ))}

              {!quizResult ? (
                <button
                  className="btn btn-primary"
                  onClick={handleSubmitAnswers}
                  disabled={loading}
                >
                  {loading ? '评判中...' : '提交答案'}
                </button>
              ) : (
                <>
                  {/* Quiz judgment results */}
                  {(quizResult.quiz_answers || []).map((qa: QuizAnswerItem, idx: number) => (
                    <div key={idx} className={styles.quizQuestionCard}>
                      {/* 题目 */}
                      <div className={styles.quizQuestionText}>
                        <span className={styles.quizQuestionNumber}>{idx + 1}</span>
                        <div
                          style={{ flex: 1 }}
                          dangerouslySetInnerHTML={{
                            __html: renderMarkdown(
                              quizAssessment?.quiz_questions?.[idx]?.question || '',
                            ),
                          }}
                        />
                      </div>
                      {/* 用户答案回显 */}
                      {qa.answer && (
                        <div
                          style={{
                            marginTop: 'var(--space-sm)',
                            padding: 'var(--space-sm) var(--space-md)',
                            fontSize: '0.875rem',
                            borderLeft: '3px solid var(--color-text-tertiary)',
                            color: 'var(--color-text-secondary)',
                          }}
                        >
                          <strong style={{ color: 'var(--color-text-primary)' }}>你的回答：</strong>
                          <div dangerouslySetInnerHTML={{ __html: renderMarkdown(qa.answer) }} />
                        </div>
                      )}
                      {/* 评判结果 */}
                      <div
                        style={{
                          marginTop: 'var(--space-sm)',
                          fontSize: '0.875rem',
                          fontWeight: 600,
                          color: 'var(--color-text-secondary)',
                        }}
                      >
                        评判结果
                      </div>
                      {renderScoreBar('准确性', qa.judgment?.accuracy_score || 0)}
                      {renderScoreBar('完整性', qa.judgment?.completeness_score || 0)}
                      {renderScoreBar('深度', qa.judgment?.depth_score || 0)}
                      {qa.judgment?.feedback && (
                        <div
                          style={{
                            marginTop: 'var(--space-sm)',
                            padding: 'var(--space-sm) var(--space-md)',
                            background: 'var(--color-accent-light)',
                            borderRadius: 'var(--radius-sm)',
                            fontSize: '0.875rem',
                            borderLeft: '3px solid var(--color-accent)',
                          }}
                        >
                          {qa.judgment.feedback}
                        </div>
                      )}
                    </div>
                  ))}
                  <div className={styles.scoreSummaryCard}>
                    <div className={styles.scoreSummaryNumber}>{quizResult.overall_score}</div>
                    <div className={styles.scoreSummaryLabel}>综合评分</div>
                    {quizResult.suggestions && (
                      <div
                        style={{
                          fontSize: '0.875rem',
                          marginTop: 'var(--space-md)',
                          color: 'var(--color-text-secondary)',
                          textAlign: 'left',
                        }}
                        dangerouslySetInnerHTML={{ __html: renderMarkdown(quizResult.suggestions) }}
                      />
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
