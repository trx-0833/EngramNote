/**
 * @file 复习答题页面
 * @description 基于间隔重复算法的复习答题界面，支持选择题、填空题和简答题。
 * 用户逐题作答，提交后即时显示正误判断和解析，SM-2 算法自动更新复习间隔。
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  getDueQuizzes,
  submitAnswer,
  getReviewStats,
  DueQuiz,
  SubmitAnswerResponse,
  ReviewStats,
} from '../api/client';
// 共享答题卡片组件（类型/难度标签与颜色由组件内部统一渲染）
import QuizAnswerCard from '../components/quiz/QuizAnswerCard';
// 与卡片复习页共用的进度条与回车键约定（5.12）
import ReviewProgress from '../components/quiz/ReviewProgress';
import { useReviewKeyboard } from '../components/quiz/useReviewKeyboard';
import { useSelfRating } from '../hooks/useSelfRating';
import { useToast } from '../components/Toast';

/** 单题答题状态 */
interface QuizState {
  quiz: DueQuiz;
  userAnswer: string;
  submitted: boolean;
  result: SubmitAnswerResponse | null;
  startTime: number;
}

/**
 * 读取异常对象上的后端 error_code（阶段 0.11）
 *
 * 用**结构化读字段**而不是 `instanceof ApiError`：本页依赖的 `../api/client`
 * 在测试里被整体 mock（`vi.mock('../api/client', ...)`），从被 mock 的模块
 * import 进来的类会是 undefined，那时 `e instanceof undefined` 直接抛
 * TypeError。字段读取对模块替换免疫。
 */
function errorCodeOf(e: unknown): string | null {
  if (typeof e !== 'object' || e === null) return null;
  const code = (e as { code?: unknown }).code;
  return typeof code === 'string' && code ? code : null;
}

export default function Review() {
  const toast = useToast();
  const [quizzes, setQuizzes] = useState<QuizState[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [completed, setCompleted] = useState(false);
  const [sessionCorrect, setSessionCorrect] = useState(0);
  const [sessionTotal, setSessionTotal] = useState(0);
  /** 提交 in-flight 锁（防双击重复提交），见 docs/decisions.md#F-23 */
  const submittingRef = useRef(false);

  // 四档自评：补完简答题的占位记录并推进 SM-2 调度
  const onRated = useCallback(
    (result: SubmitAnswerResponse) => {
      setQuizzes((prev) => prev.map((q) => (q.quiz.id === result.quiz_id ? { ...q, result } : q)));
      if (result.is_correct) setSessionCorrect((prev) => prev + 1);
      setSessionTotal((prev) => prev + 1);
      // 自评会推进调度并写入今日完成数，刷新统计
      getReviewStats()
        .then(setStats)
        .catch(() => {
          /* 统计刷新失败不影响答题 */
        });
      toast.success('自评已记录，复习进度已更新');
    },
    [toast],
  );

  const onRateError = useCallback(
    (message: string) => {
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
    submit: submitAnswer,
    onRated,
    onError: onRateError,
  });

  // 语义判分开关（阶段 3.5）：见 handleSubmit 里"为什么放在页面而不是卡片"的说明
  const [semanticGrading, setSemanticGrading] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    try {
      setLoading(true);
      const [dueData, statsData] = await Promise.all([getDueQuizzes(50), getReviewStats()]);
      setQuizzes(
        dueData.items.map((q) => ({
          quiz: q,
          userAnswer: '',
          submitted: false,
          result: null,
          startTime: Date.now(),
        })),
      );
      setStats(statsData);
      if (dueData.items.length === 0) {
        setCompleted(true);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit() {
    // in-flight 锁，防止双击/连按回车重复提交（重复 ReviewLog + SM-2 叠加），见 docs/decisions.md#F-23
    if (submittingRef.current) return;
    const current = quizzes[currentIndex];
    if (!current || current.submitted) return;
    if (!current.userAnswer.trim()) return;

    submittingRef.current = true;
    const timeSpent = Date.now() - current.startTime;

    try {
      // 语义判分只在首次提交时按用户的勾选请求（带自评的那次后端会直接跳过 LLM）。
      // 开关状态放在页面上而不是卡片里：回车提交也走这个函数，藏在卡片里
      // 会让"勾了框再按回车"静默按不判分提交。
      const result = await submitAnswer(
        current.quiz.id,
        current.userAnswer,
        timeSpent,
        undefined,
        semanticGrading,
      );
      const newQuizzes = [...quizzes];
      newQuizzes[currentIndex] = { ...current, submitted: true, result };
      setQuizzes(newQuizzes);

      // 只有真正推进了调度的提交才计入本次会话统计。
      // grading_method='ungraded' 是简答题的占位提交（尚未自评、未推进 SM-2），
      // 把它算作一次"已答"会让会话正确率虚高（占位判分恒为错误，反而虚低）。
      if (result.grading_method !== 'ungraded') {
        if (result.is_correct) setSessionCorrect((prev) => prev + 1);
        setSessionTotal((prev) => prev + 1);
      }

      // 刷新统计（更新今日已完成数）
      const newStats = await getReviewStats();
      setStats(newStats);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '提交失败';
      // 达到每日限额：按后端**错误码**分流，不再匹配中文文案（阶段 0.11 / F-19）。
      // 后端 `POST /api/review/submit` 在额度用尽时返回
      // `error_code="DAILY_REVIEW_LIMIT_REACHED"`（文案中的数字会变，码不会）。
      if (errorCodeOf(e) === 'DAILY_REVIEW_LIMIT_REACHED') {
        // 达到每日限额，跳到完成页面
        setCompleted(true);
        const newStats = await getReviewStats();
        setStats(newStats);
      } else {
        setError(msg);
      }
    } finally {
      submittingRef.current = false;
    }
  }

  /** 四档自评：把质量分回传后端，补完占位记录并推进 SM-2 调度 */
  const handleSelfRate = useCallback(
    async (quality: number) => {
      const current = quizzes[currentIndex];
      if (!current || isRated(current.quiz.id)) return;
      const timeSpent = Date.now() - current.startTime;
      await submitRating(current.quiz.id, current.userAnswer, timeSpent, quality);
    },
    [quizzes, currentIndex, isRated, submitRating],
  );

  function handleNext() {
    const current = quizzes[currentIndex];
    // 等待自评时不允许跳到下一题：此刻 SM-2 调度尚未推进，
    // 放行会让这道题永远停在"答了但结不了账"的状态。
    if (current?.result?.needs_self_assessment && !isRated(current.quiz.id)) return;
    if (currentIndex < quizzes.length - 1) {
      const nextIndex = currentIndex + 1;
      setCurrentIndex(nextIndex);
      // 重置下一题的开始时间
      const newQuizzes = [...quizzes];
      newQuizzes[nextIndex] = { ...newQuizzes[nextIndex], startTime: Date.now() };
      setQuizzes(newQuizzes);
    } else {
      setCompleted(true);
    }
  }

  /**
   * 回车 = 推进当前这一步：已提交则下一题，否则提交答案
   *
   * ⚠️ 这里曾经有一段"聚焦下一题的输入框"的 setTimeout，但 `inputRef` /
   * `textareaRef` 从未接到 `QuizAnswerCard` 上（组件不接受 ref），
   * 所以它一直在对 null 调 focus —— 看着像功能，实际是死代码，本轮删除。
   * 真正的聚焦由卡片的 `fillAutoFocus` 承担。
   */
  function handleEnter() {
    const cur = quizzes[currentIndex];
    if (cur?.submitted) {
      handleNext();
    } else {
      void handleSubmit();
    }
  }

  // 焦点刻意不收回容器：这一页的焦点应当留在填空/简答输入框里（见 hook 的说明）
  const { containerRef, handleKeyDown } = useReviewKeyboard({ onEnter: handleEnter });

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>加载复习题目中...</div>;
  }

  // 完成页面
  if (completed) {
    // 每日限额从后端 /review/stats 读取（单一来源），见 docs/decisions.md#F-12
    const dailyLimit = stats?.daily_limit ?? 10;
    const todayDone = stats?.today_done ?? 0;
    const reachedDailyLimit = todayDone >= dailyLimit;

    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <h2>复习完成</h2>
        <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
          <h3>本次复习统计</h3>
          <p>答题数: {sessionTotal}</p>
          <p>正确数: {sessionCorrect}</p>
          <p>正确率: {sessionTotal > 0 ? Math.round((sessionCorrect / sessionTotal) * 100) : 0}%</p>
        </div>

        {stats && (
          <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
            <h3>总体统计</h3>
            <p>
              今日已完成: {todayDone} / {dailyLimit}
            </p>
            <p>今日正确率: {stats.today_accuracy}%</p>
            <p>待复习题目: {stats.due_count}</p>
            <p>累计复习次数: {stats.total_reviews}</p>
            <p>累计正确率: {stats.total_accuracy}%</p>
            <p>总题目数: {stats.total_quizzes}</p>
          </div>
        )}

        {reachedDailyLimit ? (
          <div
            className="card card-accent-warning"
            style={{ textAlign: 'center', marginBottom: 'var(--space-md)' }}
          >
            {/* 原值 `#ff9800`（白底 2.16:1）与 F-36 的「中」徽章同色值，
                换成 `--color-warning` 的取值（#936408，白底 5.16:1）。 */}
            <p style={{ fontWeight: 600, color: 'var(--color-warning)' }}>
              今日已完成 {dailyLimit} 道题，休息一下吧！
            </p>
            <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)' }}>
              明天再来继续复习
            </p>
          </div>
        ) : (
          <button className="btn btn-primary" onClick={loadData}>
            继续复习
          </button>
        )}
      </div>
    );
  }

  if (error && quizzes.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
        <p style={{ color: 'var(--color-error)' }}>{error}</p>
        <button className="btn btn-primary" onClick={loadData}>
          重试
        </button>
      </div>
    );
  }

  const current = quizzes[currentIndex];
  if (!current) return null;

  const quiz = current.quiz;

  return (
    <div
      className="page-enter"
      ref={containerRef}
      onKeyDown={handleKeyDown}
      style={{ maxWidth: 700, margin: '0 auto' }}
    >
      {/* 页面标题（a11y-audit **F-32**：这一页此前 `<h1>`~`<h6>` 数量为 **0**，
          axe 判 page-has-heading-one）。
          ⚠️ 补 h1 时要一起看**层级**：这一页在答题态下除此之外**没有任何标题**
          （`QuizAnswerCard` 里全是 p/span/button），所以补上 h1 就是完整的
          大纲，不存在上一轮踩到的 `h1 → h3` 跳级（§8.4 第 1 条）。
          为什么写在页面里而不是用 `ReviewProgress` 的 `title`：那个组件的文件头
          写明了两种排版的分工 ——"**答题复习侧：页面标题在别处**（或没有），
          进度条与两侧计数排在同一行"。也就是说标题本来就该长在这一页上，
          只是一直没写；传 `title` 会把进度条挤到第二行，那是改版式，
          不是修可访问性。
          标题用词取自项目里已有的说法：`CardReview.tsx` 里指到这一页的链接
          文案就是「答题复习」（`ReviewProgress` 的文件头也这么称呼它）。 */}
      <h1
        className="heading-serif gradient-text"
        style={{ fontSize: '1.5rem', marginBottom: 'var(--space-lg)' }}
      >
        答题复习
      </h1>

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
          <>
            {sessionCorrect}/{sessionTotal} 正确 | 今日 {stats?.today_done ?? 0}/
            {stats?.daily_limit ?? 10}
          </>
        }
      />

      {/* 题目卡片（共享 QuizAnswerCard；提交竞态锁由 handleSubmit 的 submittingRef 承担，见 docs/decisions.md#F-23） */}
      <QuizAnswerCard
        quiz={quiz}
        userAnswer={current.userAnswer}
        submitted={current.submitted}
        result={current.result}
        submitting={submittingRef.current}
        showSm2Info
        showReviewMeta
        isLast={currentIndex >= quizzes.length - 1}
        selfRated={isRated(quiz.id)}
        selfRatingSubmitting={ratingSubmitting}
        semanticGrading={semanticGrading}
        onToggleSemanticGrading={setSemanticGrading}
        onSelectAnswer={(answer) => {
          const newQuizzes = [...quizzes];
          newQuizzes[currentIndex] = { ...current, userAnswer: answer };
          setQuizzes(newQuizzes);
        }}
        onSubmit={handleSubmit}
        onSelfRate={handleSelfRate}
        onSkipSelfRate={() => skipRating(quiz.id)}
        onNext={handleNext}
      />
    </div>
  );
}
