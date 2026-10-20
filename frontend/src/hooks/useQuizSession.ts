/**
 * @file 刷题会话状态机（想法 8 阶段 2）
 *
 * ## 为什么抽它
 *
 * 三个刷题页（答题复习 / 快速复习 / 今日学习）各自手写了一份**逐字重复**的
 * 会话逻辑：`quizzes[]` / `currentIndex` / `sessionCorrect` / `sessionTotal` /
 * `completed` / `semanticGrading` / `submittingRef`，加上 `handleSubmit`、
 * `handleNext`、`goTo`、`handleSelfRate`、`handleEnter` —— 五份状态 + 五个函数，
 * 每个页面写一遍。本 hook 落地前它们分别是 297 / 318 / 656 行。
 *
 * 重复的代价不只是行数。**同一个缺陷要在三处各修一遍，而漏掉一处不会被发现**：
 * 本文件落地时就撞上一个真实例子 —— 原 `handleNext` 每次都重置 `startTime`，
 * 加上"往前翻"之后它会把回看时间算进作答耗时。三份实现里改对两份、
 * 漏掉一份，测试也未必红（只有那一页的用例覆盖到才红）。
 *
 * ## 它**不**管什么（刻意留在页面里）
 *
 * - **题目从哪来**：三页的取数接口不同（`getDueQuizzes` / `getQuickReview`），
 *   而且各自还有别的数据要拉（统计、日报、薄弱点）。取数留在页面，
 *   用 `replaceAll()` 把结果灌进来。
 * - **提交打到哪个端点**：由 `submit` 参数注入。
 * - **提交成功后要不要刷新统计**：由 `onRated` 参数注入。
 *
 * ## 计时规则（唯一一份，因此不会漂移）
 *
 * 装载时**只有第 1 题**起算（用户此刻正看着它），其余为 `0` = "从未到过"，
 * 由 `goTo` 第一次到达时才置。若装载时全填 `Date.now()`，在第 1 题停留
 * 5 分钟后翻到第 2 题，第 2 题的"作答耗时"会包含那 5 分钟 ——
 * 而 `time_spent_ms` 会被 `report_service` 汇总成学习时长。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import type { SubmitAnswerResponse } from '../api/client';
import { useToast } from '../components/Toast';
import { useTranslation } from '../i18n';
import { useSelfRating } from './useSelfRating';

/** 单题在会话里的状态 */
export interface QuizSessionItem<TQuiz> {
  quiz: TQuiz;
  userAnswer: string;
  submitted: boolean;
  result: SubmitAnswerResponse | null;
  /**
   * 本题开始作答的时刻（毫秒）。
   *
   * `0` = **从未到过**（哨兵值，不是"1970 年"）。见文件头的计时规则。
   */
  startTime: number;
}

/** 会话里的一题至少要有 id（自评与结果回填都按它匹配） */
interface HasId {
  id: string;
}

interface UseQuizSessionOptions<TQuiz extends HasId> {
  /**
   * 提交一次作答。三页各自注入自己的端点。
   *
   * 传的是**整个题目对象**而不是 id：调用方常常还要按题目分派
   * （例如快速复习要带上 `noteId`），只给 id 会逼它们再回查一次。
   */
  submit: (
    quiz: TQuiz,
    userAnswer: string,
    timeSpentMs: number,
    selfRating: number | undefined,
    semanticGrading?: boolean,
  ) => Promise<SubmitAnswerResponse>;
  /**
   * 自评成功后的额外动作（可选）
   *
   * 答题复习与今日学习用它刷新统计（自评会推进调度并写入今日完成数）；
   * 快速复习走独立入口、不显示今日额度，因此不传。
   *
   * ⚠️ 会话自身的状态更新（回填 `result`、累加统计）由本 hook 负责，
   * **不**在这里做 —— 否则每个页面都要重写一遍同样的三行。
   */
  onRated?: (result: SubmitAnswerResponse) => void;
  /** 自评失败时的额外动作（可选）；hook 自身只负责 toast */
  onRateError?: (message: string) => void;
  /**
   * 提交**成功**后的额外动作（可选）
   *
   * 答题复习与今日学习用它刷新统计（今日已完成数变了）；快速复习
   * 不受每日额度约束、页面也不显示那个数字，因此不传。
   */
  onSubmitted?: () => void;
  /**
   * 提交失败时的额外动作（可选）
   *
   * ## 为什么需要它
   *
   * hook 自己只会弹一条 toast —— 那对"网络抖了一下"是对的，但**每日限额用尽
   * 不是错误**，是"今天做完了"：答题复习页要据此切到完成页并刷新统计。
   * 这个分流依赖后端的 `error_code`，而 `error_code` 的读法各页不同
   * （答题复习用结构化读字段，见 `Review.tsx` 的 `errorCodeOf`），
   * 所以判断留给页面，hook 只负责把异常递出去。
   */
  onSubmitError?: (error: unknown) => void;
}

export function useQuizSession<TQuiz extends HasId>({
  submit,
  onRated,
  onRateError,
  onSubmitted,
  onSubmitError,
}: UseQuizSessionOptions<TQuiz>) {
  const { t } = useTranslation();
  const toast = useToast();

  const [quizzes, setQuizzes] = useState<QuizSessionItem<TQuiz>[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [sessionCorrect, setSessionCorrect] = useState(0);
  const [sessionTotal, setSessionTotal] = useState(0);
  const [completed, setCompleted] = useState(false);
  /**
   * 语义判分开关（阶段 3.5，仅简答题有意义）
   *
   * ⚠️ 状态**必须**在这里（而不是藏在卡片里）：回车提交的处理函数
   * （`handleEnter`）在本 hook 内，若开关只在卡片内部，用户勾了框再按回车
   * 就会静默按"不判分"提交 —— "选了但没生效"是最难被发现的一类不一致。
   */
  const [semanticGrading, setSemanticGrading] = useState(false);
  /**
   * 提交 in-flight 标记（防双击重复提交），见 docs/decisions.md#F-23
   *
   * ## 为什么是 state 而不是 ref
   *
   * 原三页用的是 `useRef`，并在**渲染期**读 `submittingRef.current` 传给
   * `QuizAnswerCard` 的 `submitting` —— 那是"渲染期读 ref"，React Compiler
   * 的 `react-hooks/refs` 规则判为 error（`QuickReview.tsx` 原先靠一条
   * `eslint-disable` 压着，而那条指令后来失效了，见该文件的说明）。
   *
   * 改成 state 后：
   * - `submitting` state 负责**驱动 UI**（禁用按钮、置灰）—— 这是渲染期读的；
   * - `submittingRef` 负责**同一帧内的同步判重**（双击/连按回车）——
   *   `setSubmitting(true)` 不会立刻改变本次闭包里的 `submitting`，
   *   所以判重必须靠 ref。
   *
   * 两者职责不同、都要留。ref 只在事件处理函数里读写，不碰渲染期，
   * `react-hooks/refs` 规则就不会报。
   */
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  /**
   * 回调放进 ref：调用方每次渲染都传新的箭头函数，
   * 若直接进 `useCallback` 依赖，`handleSubmit` 等会每帧换身份，
   * 而它们又被 `useReviewKeyboard` 等消费 —— 白白让那些 hook 反复重算。
   *
   * ⚠️ 用 `useEffect` 同步而**不是**在渲染期直接赋值（`ref.current = x`）：
   * 后者是"渲染期写 ref"，同样被 `react-hooks/refs` 判为 error。
   * effect 在提交后跑，而回调只在用户交互时被调用 —— 那时 effect 早已执行。
   */
  const submitRef = useRef(submit);
  const onRatedRef = useRef(onRated);
  const onSubmittedRef = useRef(onSubmitted);
  useEffect(() => {
    submitRef.current = submit;
    onRatedRef.current = onRated;
    onSubmittedRef.current = onSubmitted;
  }, [submit, onRated, onSubmitted]);

  /**
   * 题目查找表，给 `useSelfRating` 的回调用
   *
   * 那个回调只拿到 `quizId`（它的接口是给"卡片/题目"通用场景设计的），
   * 而我们的 `submit` 收整个题目对象，所以要能按 id 查回来。
   * 用 effect 维护而不是渲染期写 ref（同上）。
   */
  const quizByIdRef = useRef<Map<string, TQuiz>>(new Map());
  useEffect(() => {
    quizByIdRef.current = new Map(quizzes.map((q) => [q.quiz.id, q.quiz]));
  }, [quizzes]);

  /** 把 API 返回的一批题目变成会话初始状态 */
  const makeItems = useCallback(
    (items: TQuiz[]): QuizSessionItem<TQuiz>[] =>
      items.map((q, i) => ({
        quiz: q,
        userAnswer: '',
        submitted: false,
        result: null,
        // 只有第 1 题在装载时起算 —— 见文件头的计时规则
        startTime: i === 0 ? Date.now() : 0,
      })),
    [],
  );

  /** 灌入/替换整批题目（页面取数成功后调用） */
  const replaceAll = useCallback(
    (items: TQuiz[]) => {
      setQuizzes(makeItems(items));
      setCurrentIndex(0);
      setSessionCorrect(0);
      setSessionTotal(0);
      setCompleted(false);
    },
    [makeItems],
  );

  /** 清空会话（"再复习一轮"取到 0 题时用） */
  const clear = useCallback(() => {
    setQuizzes([]);
    setCurrentIndex(0);
    setSessionCorrect(0);
    setSessionTotal(0);
    setCompleted(false);
  }, []);

  /**
   * 自评成功：回填结果 + 累加统计 + 交给页面做额外动作
   *
   * ⚠️ 这里**不**写 `submitted`：自评补的是一条已提交的占位记录，
   * `submitted` 早在首次提交时就是 `true` 了。
   */
  const handleRated = useCallback(
    (result: SubmitAnswerResponse) => {
      setQuizzes((prev) => prev.map((q) => (q.quiz.id === result.quiz_id ? { ...q, result } : q)));
      if (result.is_correct) setSessionCorrect((prev) => prev + 1);
      setSessionTotal((prev) => prev + 1);
      toast.success(t('quickReview.selfRatingRecorded'));
      onRatedRef.current?.(result);
    },
    [toast, t],
  );

  const handleRateError = useCallback(
    (message: string) => {
      toast.error(message);
      onRateError?.(message);
    },
    [toast, onRateError],
  );

  const {
    submitRating,
    submitting: ratingSubmitting,
    isRated,
    skipRating,
  } = useSelfRating({
    submit: (quizId, userAnswer, timeSpentMs, selfRating) => {
      // `useSelfRating` 只回传 id，这里查回题目对象再交给页面注入的 submit
      const quiz = quizByIdRef.current.get(quizId);
      if (!quiz) {
        return Promise.reject(new Error(`会话里找不到题目 ${quizId}`));
      }
      return submitRef.current(quiz, userAnswer, timeSpentMs, selfRating);
    },
    onRated: handleRated,
    onError: handleRateError,
  });

  /**
   * 跳到某一题（上一题 / 下一题 / 题号条共用）
   *
   * ## 两条规则，都是刻意的
   *
   * 1. **往前翻不受"等待自评"约束。** 自评是"给这道题结账"，往回翻是
   *    "去看别处"，两者无关。把上一题也锁住会让用户卡在一道不想现在评的题上。
   * 2. **往后跳受约束。** 与「下一题」按钮一致 —— 不能借题号条绕过它，
   *    否则那道题会永远停在未结算状态（`overhaul-plan.md` 阶段 1.4）。
   *
   * ## 计时只在**第一次**到达时起算（见文件头）
   */
  const goTo = useCallback(
    (target: number) => {
      if (target < 0 || target >= quizzes.length || target === currentIndex) return;
      if (target > currentIndex) {
        const cur = quizzes[currentIndex];
        if (cur?.result?.needs_self_assessment && !isRated(cur.quiz.id)) return;
      }
      setCurrentIndex(target);
      // 计时只在**第一次**到达时起算（见文件头）
      if (quizzes[target].startTime === 0) {
        setQuizzes((prev) => {
          const next = [...prev];
          next[target] = { ...next[target], startTime: Date.now() };
          return next;
        });
      }
    },
    [quizzes, currentIndex, isRated],
  );

  const handlePrev = useCallback(() => goTo(currentIndex - 1), [goTo, currentIndex]);

  /** 下一题；已是最后一题则进汇总页 */
  const handleNext = useCallback(() => {
    const cur = quizzes[currentIndex];
    // 等待自评时不允许推进：此刻调度尚未推进，
    // 放行会让这道题永远停在"答了但结不了账"的状态。
    if (cur?.result?.needs_self_assessment && !isRated(cur.quiz.id)) return;
    if (currentIndex < quizzes.length - 1) {
      goTo(currentIndex + 1);
      return;
    }
    setCompleted(true);
  }, [quizzes, currentIndex, isRated, goTo]);

  /** 提交当前题的答案 */
  const handleSubmit = useCallback(async () => {
    // in-flight 锁，防止双击/连按回车重复提交，见 docs/decisions.md#F-23
    if (submittingRef.current) return;
    const current = quizzes[currentIndex];
    if (!current || current.submitted || !current.userAnswer.trim()) return;

    submittingRef.current = true;
    setSubmitting(true);
    const timeSpent = Date.now() - current.startTime;
    try {
      // 语义判分只在首次提交时按用户的勾选请求
      // （带自评的那次后端会直接跳过 LLM）
      const result = await submitRef.current(
        current.quiz,
        current.userAnswer,
        timeSpent,
        undefined,
        semanticGrading,
      );
      setQuizzes((prev) => {
        const next = [...prev];
        next[currentIndex] = { ...next[currentIndex], submitted: true, result };
        return next;
      });
      // 只有真正推进了调度的提交才计入本次统计；简答题的占位提交
      // （grading_method='ungraded'，尚未自评）不算一次"已答"。
      if (result.grading_method !== 'ungraded') {
        if (result.is_correct) setSessionCorrect((prev) => prev + 1);
        setSessionTotal((prev) => prev + 1);
      }
      onSubmittedRef.current?.();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : t('quickReview.submitFailed'));
      onSubmitError?.(e);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }, [quizzes, currentIndex, semanticGrading, toast, t, onSubmitError]);

  /** 四档自评：补完占位记录并推进调度 */
  const handleSelfRate = useCallback(
    async (quality: number) => {
      const current = quizzes[currentIndex];
      if (!current || isRated(current.quiz.id)) return;
      const timeSpent = Date.now() - current.startTime;
      await submitRating(current.quiz.id, current.userAnswer, timeSpent, quality);
    },
    [quizzes, currentIndex, isRated, submitRating],
  );

  /** 更新当前题的作答内容 */
  const setAnswer = useCallback(
    (answer: string) => {
      setQuizzes((prev) => {
        const next = [...prev];
        next[currentIndex] = { ...next[currentIndex], userAnswer: answer };
        return next;
      });
    },
    [currentIndex],
  );

  /** 回车 = 推进当前这一步：已提交则下一题，否则提交答案（三页同一约定） */
  const handleEnter = useCallback(() => {
    const current = quizzes[currentIndex];
    if (current?.submitted) handleNext();
    else void handleSubmit();
  }, [quizzes, currentIndex, handleNext, handleSubmit]);

  return {
    // 状态
    quizzes,
    currentIndex,
    current: quizzes[currentIndex] as QuizSessionItem<TQuiz> | undefined,
    sessionCorrect,
    sessionTotal,
    completed,
    semanticGrading,
    /** 是否正在提交（渲染期读这个，**不要**读 `submittingRef`） */
    submitting,
    submittingRef,
    // 自评（透传 useSelfRating）
    isRated,
    ratingSubmitting,
    skipRating,
    // 操作
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
    /** 让页面在取到新数据后能直接置完成态（如"今日无到期题目"） */
    setCompleted,
  };
}
