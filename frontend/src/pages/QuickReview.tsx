/**
 * @file 快速复习页面
 * @description 按笔记维度快速复习，用户上传笔记并完成理解后，
 * 可立即复习该笔记关联的所有题目。支持选择题、填空题和简答题，
 * 逐题展示，提交后显示判分结果和解析，最终汇总统计。
 *
 * ## 会话逻辑住在 `hooks/useQuizSession`
 *
 * 本页只保留独有部分：按 `noteId` 取题、空状态/错误态的排版。
 * 与答题复习、今日学习共用同一份会话状态机 —— 理由见那个 hook 的文件头。
 *
 * ## 与答题复习的两处**刻意**不同
 *
 * 1. **不受每日额度约束**（后端 `skip_daily_limit=True`），所以本页既不显示
 *    额度、也没有"额度用尽"的分支；
 * 2. **走独立提交入口** `submitQuickReviewAnswer`，它带 `noteId`。
 */
import { useCallback, useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { getQuickReview, submitQuickReviewAnswer, type QuickQuiz } from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
// 共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard';
// 与卡片复习页共用的进度条与回车键约定（5.12）
import ReviewProgress from '../components/quiz/ReviewProgress';
// 题号导航条（想法 8）：可前后翻、可点题号跳转
import QuizNavigator, { type QuizNavItem } from '../components/quiz/QuizNavigator';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变
import PageHeader from '../components/PageHeader';
import { useReviewKeyboard } from '../components/quiz/useReviewKeyboard';
import { useQuizSession } from '../hooks/useQuizSession';
import { useTranslation } from '../i18n';

export default function QuickReview() {
  const { t } = useTranslation();
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  /** 提交作答：本页走带 `noteId` 的独立入口（不受每日额度约束） */
  const submit = useCallback(
    (quiz: QuickQuiz, userAnswer: string, timeSpentMs: number, selfRating?: number) =>
      submitQuickReviewAnswer(noteId ?? '', quiz.id, userAnswer, timeSpentMs, selfRating),
    [noteId],
  );

  const session = useQuizSession<QuickQuiz>({
    // 进度持久化：**必须带 noteId** —— 不同笔记是不同会话，
    // 共用一个 scope 会让它们互相覆盖
    scope: `quick:${noteId ?? ''}`,
    submit,
  });
  const {
    quizzes,
    currentIndex,
    current,
    sessionCorrect,
    sessionTotal,
    completed,
    semanticGrading,
    submitting,
    isRated,
    ratingSubmitting,
    skipRating,
    replaceAll,
    clear,
    goTo,
    handlePrev,
    handleNext,
    handleSubmit,
    handleSelfRate,
    handleEnter,
    setAnswer,
    setSemanticGrading,
  } = session;

  const loadQuizzes = useCallback(async () => {
    if (!noteId) return;
    setLoading(true);
    setError('');
    try {
      const data = await getQuickReview(noteId);
      if (data.items.length === 0) clear();
      else replaceAll(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('quickReview.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [noteId, t, clear, replaceAll]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadQuizzes();
  }, [noteId, loadQuizzes]);

  /** 题号条的数据：序号 + 是否已答 + 是否答对 */
  const navItems: QuizNavItem[] = quizzes.map((q, i) => ({
    index: i,
    submitted: q.submitted,
    isCorrect: q.result?.is_correct,
  }));

  // 焦点刻意不收回容器：这一页的焦点应当留在填空/简答输入框里（见 hook 的说明）
  const { containerRef, handleKeyDown } = useReviewKeyboard({ onEnter: handleEnter });

  // --- 加载中 ---
  if (loading) return <LoadingSpinner text={t('quickReview.loading')} />;

  // --- 错误 ---
  if (error && quizzes.length === 0) return <ErrorDisplay message={error} onRetry={loadQuizzes} />;

  // --- 无题目 ---
  if (!loading && quizzes.length === 0) {
    return (
      <div className="page-enter" style={{ margin: '0 auto' }}>
        <EmptyState
          message={t('quickReview.empty')}
          description={t('quickReview.emptyHint')}
          action={
            <button className="btn btn-secondary" onClick={() => navigate(`/notes/${noteId}`)}>
              {t('quickReview.backToNote')}
            </button>
          }
        />
      </div>
    );
  }

  // --- 答题完成汇总 ---
  if (completed) {
    const accuracy = sessionTotal > 0 ? Math.round((sessionCorrect / sessionTotal) * 100) : 0;
    return (
      <div className="page-enter" style={{ margin: '0 auto' }}>
        <h2 style={{ marginBottom: 'var(--space-lg)' }}>{t('quickReview.done')}</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>{t('quickReview.sessionStats')}</h3>
          <p>{t('quickReview.answerCount', { count: sessionTotal })}</p>
          <p>{t('quickReview.correctCount', { count: sessionCorrect })}</p>
          <p>{t('quickReview.accuracy', { percent: accuracy })}</p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <button className="btn btn-primary" onClick={() => navigate(`/notes/${noteId}`)}>
            {t('quickReview.backToNote')}
          </button>
          {/* 重新从 API 获取题目，避免重复走 SM-2 调度 */}
          <button className="btn btn-secondary" onClick={loadQuizzes}>
            {t('quickReview.again')}
          </button>
        </div>
      </div>
    );
  }

  // --- 答题中 ---
  if (!current) return null;
  const quiz = current.quiz;

  return (
    <div
      className="page-enter"
      ref={containerRef}
      onKeyDown={handleKeyDown}
      style={{ margin: '0 auto' }}
    >
      {/* 页面标题（与已修的 F-32「答题复习」同一处判据：这一页此前
          `<h1>`~`<h6>` 数量为 0，axe 判 page-has-heading-one）。
          词取自项目里已有的说法：`ReviewProgress` 的文件头把四条复习流程
          列成「答题复习 / **快速复习** / 今日学习 / 卡片复习」。
          同样刻意**不用** `ReviewProgress` 的 `title`：那会把进度条挤到第二行，
          而答题侧的排版本来就是"标题在别处 + 一行式进度条"（见该组件文件头）。
          批次 C1：量尺交给 `<PageHeader>`（字号本就 1.5rem，观感不变）。 */}
      <PageHeader title={t('quickReview.title')} />

      {/* 进度条（与卡片复习页共用） */}
      <ReviewProgress
        index={currentIndex}
        total={quizzes.length}
        done={current.submitted}
        label={
          <>
            {currentIndex + 1} / {quizzes.length}
          </>
        }
        trailing={
          <>{t('quickReview.headerProgress', { correct: sessionCorrect, total: sessionTotal })}</>
        }
      />
      {/* 题号导航条（想法 8）：可点题号跳转，四态可视 */}
      <QuizNavigator items={navItems} currentIndex={currentIndex} onJump={goTo} />
      {/* 题目卡片（共享 QuizAnswerCard；快速复习不展示 SM-2 信息） */}
      <QuizAnswerCard
        quiz={quiz}
        userAnswer={current.userAnswer}
        submitted={current.submitted}
        result={current.result}
        submitting={submitting}
        showSm2Info={false}
        semanticGrading={semanticGrading}
        onToggleSemanticGrading={setSemanticGrading}
        fillAutoFocus
        isLast={currentIndex >= quizzes.length - 1}
        selfRated={isRated(quiz.id)}
        selfRatingSubmitting={ratingSubmitting}
        onSelectAnswer={setAnswer}
        onSubmit={handleSubmit}
        onSelfRate={handleSelfRate}
        onSkipSelfRate={() => skipRating(quiz.id)}
        onNext={handleNext}
        onPrev={handlePrev}
        hasPrev={currentIndex > 0}
      />
      <div style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
        <button className="btn btn-secondary" onClick={() => navigate(`/notes/${noteId}`)}>
          {t('quickReview.backToNote')}
        </button>
      </div>
    </div>
  );
}
