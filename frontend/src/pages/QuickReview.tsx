/**
 * @file 快速复习页面
 * @description 按笔记维度快速复习，用户上传笔记并完成理解后，
 * 可立即复习该笔记关联的所有题目。支持选择题、填空题和简答题，
 * 逐题展示，提交后显示判分结果和解析，最终汇总统计。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  getQuickReview,
  submitQuickReviewAnswer,
  type QuickQuiz,
  type SubmitAnswerResponse,
} from '../api/client';
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
import { useSelfRating } from '../hooks/useSelfRating';
import { useToast } from '../components/Toast';
import { useTranslation } from '../i18n';

/** 单题答题状态 */
interface QuizState {
  quiz: QuickQuiz;
  userAnswer: string;
  submitted: boolean;
  result: SubmitAnswerResponse | null;
  startTime: number;
}

export default function QuickReview() {
  const { t } = useTranslation();
  const { noteId } = useParams<{ noteId: string }>();
  const navigate = useNavigate();
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // 答题状态
  const [quizzes, setQuizzes] = useState<QuizState[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [sessionCorrect, setSessionCorrect] = useState(0);
  const [sessionTotal, setSessionTotal] = useState(0);
  const [completed, setCompleted] = useState(false);
  // 语义判分开关（阶段 3.5）：状态由页面持有，回车提交才能用上它
  const [semanticGrading, setSemanticGrading] = useState(false);
  /** 提交 in-flight 锁（防双击重复提交），见 docs/decisions.md#F-23 */
  const submittingRef = useRef(false);

  // 四档自评：补完简答题的占位记录并推进 SM-2 调度
  const onRated = useCallback(
    (result: SubmitAnswerResponse) => {
      setQuizzes((prev) => prev.map((q) => (q.quiz.id === result.quiz_id ? { ...q, result } : q)));
      if (result.is_correct) setSessionCorrect((prev) => prev + 1);
      setSessionTotal((prev) => prev + 1);
      toast.success(t('quickReview.selfRatingRecorded'));
    },
    [toast, t],
  );
  const onRateError = useCallback(
    (message: string) => {
      setError(message);
      toast.error(message);
    },
    [toast],
  );
  const {
    submitRating,
    submitting: ratingSubmitting,
    isRated,
    skipRating,
  } = useSelfRating({
    // 快速复习走独立入口（不受每日上限约束），自评也必须走同一入口
    submit: (quizId, userAnswer, timeSpentMs, selfRating) =>
      submitQuickReviewAnswer(noteId ?? '', quizId, userAnswer, timeSpentMs, selfRating),
    onRated,
    onError: onRateError,
  });

  const loadQuizzes = useCallback(async () => {
    if (!noteId) return;
    setLoading(true);
    setError('');
    try {
      const data = await getQuickReview(noteId);
      if (data.items.length === 0) {
        setQuizzes([]);
      } else {
        setQuizzes(
          data.items.map((q, i) => ({
            quiz: q,
            userAnswer: '',
            submitted: false,
            result: null,
            // 只有**第 1 题**在装载时就开始计时（用户此刻正看着它）；
            // 其余为 0 = "从未到过"，等 `goTo` 第一次到达时才起算。
            // 若这里全填 `Date.now()`，那么在第 1 题停留 5 分钟后翻到第 2 题，
            // 第 2 题的"作答耗时"会包含那 5 分钟。
            startTime: i === 0 ? Date.now() : 0,
          })),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('quickReview.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [noteId, t]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadQuizzes();
  }, [noteId, loadQuizzes]);

  async function handleSubmit() {
    // in-flight 锁，防止双击/连按回车重复提交，见 docs/decisions.md#F-23
    if (submittingRef.current) return;
    const current = quizzes[currentIndex];
    if (!current || current.submitted || !current.userAnswer.trim()) return;
    if (!noteId) return;

    submittingRef.current = true;
    const timeSpent = Date.now() - current.startTime;
    try {
      // 语义判分只在首次提交时按用户的勾选请求（带自评的那次后端会直接跳过 LLM）
      const result = await submitQuickReviewAnswer(
        noteId,
        current.quiz.id,
        current.userAnswer,
        timeSpent,
        undefined,
        semanticGrading,
      );
      const newQuizzes = [...quizzes];
      newQuizzes[currentIndex] = { ...current, submitted: true, result };
      setQuizzes(newQuizzes);
      // 只有真正推进了调度的提交才计入本次统计；简答题的占位提交
      // （grading_method='ungraded'，尚未自评）不算一次"已答"。
      if (result.grading_method !== 'ungraded') {
        if (result.is_correct) setSessionCorrect((prev) => prev + 1);
        setSessionTotal((prev) => prev + 1);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('quickReview.submitFailed'));
    } finally {
      submittingRef.current = false;
    }
  }

  /** 四档自评：补完占位记录并推进 SM-2 调度 */
  const handleSelfRate = useCallback(
    async (quality: number) => {
      const current = quizzes[currentIndex];
      if (!current || !noteId || isRated(current.quiz.id)) return;
      const timeSpent = Date.now() - current.startTime;
      await submitRating(current.quiz.id, current.userAnswer, timeSpent, quality);
    },
    [quizzes, currentIndex, noteId, isRated, submitRating],
  );

  function handleNext() {
    const current = quizzes[currentIndex];
    // 等待自评时不允许跳到下一题：此刻调度尚未推进，
    // 放行会让这道题永远停在"答了但结不了账"的状态。
    if (current?.result?.needs_self_assessment && !isRated(current.quiz.id)) return;
    if (currentIndex < quizzes.length - 1) {
      goTo(currentIndex + 1);
    } else {
      setCompleted(true);
    }
  }

  /**
   * 跳到某一题（上一题 / 题号条共用）
   *
   * ## 两条规则，都是刻意的
   *
   * 1. **往前翻不受"等待自评"约束。** 自评是"给这道题结账"，往回翻是
   *    "去看别处"，两者无关。把上一题也锁住会让用户卡在一道不想现在评的题上。
   *    这与 `QuizAnswerCard` 里"上一题不禁用"是同一个决定。
   * 2. **往后跳仍受约束。** 与「下一题」按钮一致 —— 不能借题号条绕过它，
   *    否则那道题会永远停在未结算状态（`overhaul-plan.md` 阶段 1.4）。
   *
   * ## 计时只在**第一次**到达时起算
   *
   * 原实现在每次 `handleNext` 里都写 `startTime: Date.now()`。加回退之后
   * 那样写就有问题了：往前翻再往后翻会**重置计时**，把回看的时间也算进
   * 这道题的作答耗时（`time_spent_ms` 会被 `report_service` 汇总成学习时长）。
   * 所以只在 `startTime === 0`（从未到过）时才置。
   */
  function goTo(target: number) {
    if (target === currentIndex) return;
    if (target > currentIndex) {
      const current = quizzes[currentIndex];
      if (current?.result?.needs_self_assessment && !isRated(current.quiz.id)) return;
    }
    setCurrentIndex(target);
    const newQuizzes = [...quizzes];
    if (newQuizzes[target].startTime === 0) {
      newQuizzes[target] = { ...newQuizzes[target], startTime: Date.now() };
    }
    setQuizzes(newQuizzes);
  }

  /** 题号条的数据：序号 + 是否已答 + 是否答对 */
  const navItems: QuizNavItem[] = quizzes.map((q, i) => ({
    index: i,
    submitted: q.submitted,
    isCorrect: q.result?.is_correct,
  }));

  /** 回车 = 推进当前这一步：已提交则下一题，否则提交答案（与卡片复习页同一约定） */
  function handleEnter() {
    const current = quizzes[currentIndex];
    if (current?.submitted) handleNext();
    else void handleSubmit();
  }

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
          <button
            className="btn btn-secondary"
            onClick={async () => {
              // 重新从 API 获取题目，避免重复走 SM-2 调度
              if (!noteId) return;
              setLoading(true);
              try {
                const data = await getQuickReview(noteId);
                if (data.items.length === 0) {
                  setQuizzes([]);
                } else {
                  setQuizzes(
                    data.items.map((q, i) => ({
                      quiz: q,
                      userAnswer: '',
                      submitted: false,
                      result: null,
                      startTime: i === 0 ? Date.now() : 0,
                    })),
                  );
                  setCurrentIndex(0);
                  setSessionCorrect(0);
                  setSessionTotal(0);
                  setCompleted(false);
                }
              } catch (err) {
                setError(err instanceof Error ? err.message : t('quickReview.loadFailed'));
              } finally {
                setLoading(false);
              }
            }}
          >
            {t('quickReview.again')}
          </button>
        </div>
      </div>
    );
  }

  // --- 答题中 ---
  const current = quizzes[currentIndex];
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
        // 渲染期读取 ref 作为按钮禁用锁(提交中标记不触发重渲染,由 submit 的 setState 兜底)
        submitting={submittingRef.current}
        showSm2Info={false}
        semanticGrading={semanticGrading}
        onToggleSemanticGrading={setSemanticGrading}
        fillAutoFocus
        isLast={currentIndex >= quizzes.length - 1}
        selfRated={isRated(quiz.id)}
        selfRatingSubmitting={ratingSubmitting}
        onSelectAnswer={(answer) => {
          const newQuizzes = [...quizzes];
          newQuizzes[currentIndex] = { ...current, userAnswer: answer };
          setQuizzes(newQuizzes);
        }}
        onSubmit={handleSubmit}
        onSelfRate={handleSelfRate}
        onSkipSelfRate={() => skipRating(quiz.id)}
        onNext={handleNext}
        onPrev={() => goTo(currentIndex - 1)}
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
