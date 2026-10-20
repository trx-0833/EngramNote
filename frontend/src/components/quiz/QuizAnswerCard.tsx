/**
 * @file 共享答题卡片组件
 * @description 抽取 Review / QuickReview / TodayLearn 三页重复的题目卡片：
 * 类型/难度标签、题目内容、选择题/填空题/简答题作答区、提交按钮、
 * 判分反馈、语义判分明细、调度依据、原文语境、下一题按钮。
 * 行为差异通过 props 参数化；提交/下一题的竞态锁由页面 handleSubmit 内实现
 * （submittingRef），本组件仅透传 submitting 禁用状态，见 docs/decisions.md#F-23。
 */
import { type ReactNode } from 'react';
import type { SubmitAnswerResponse } from '../../api/client';
import SourceContext from './SourceContext';
// 四档自评控件与卡片复习页共用（5.12：两条复习流程的统一件）
import SelfRatingButtons, { useSelfRatingLabel } from './SelfRatingButtons';
// 类名由 CSS Modules 哈希化后从 styles 取（overhaul-plan 5.6 的约定）：
// 不再写全局类名字面量，否则搬迁到模块的规则会因为选择器对不上而静默失效
import styles from './QuizAnswerCard.module.css';
import Icon from '../Icon';
import { difficultyColors, useLabels } from '../../utils/labels';
import { useTranslation } from '../../i18n';

/** 答题卡片所需的最小题目结构 */
export interface QuizCardQuestion {
  question_type: string;
  question: string;
  difficulty?: string;
  options?: string | string[] | null;
  /** 复习元信息（Review 页展示） */
  review_count?: number;
  interval?: number;
  /** 卡片与笔记 ID：用于"原文语境"（阶段 3.13） */
  card_id?: string;
  note_id?: string;
}

interface QuizAnswerCardProps {
  quiz: QuizCardQuestion;
  userAnswer: string;
  submitted: boolean;
  result: SubmitAnswerResponse | null;
  /** 是否正在提交（提交中禁用按钮，见 docs/decisions.md#F-23） */
  submitting?: boolean;
  /** 是否展示 SM-2 调度信息（Review 有，快速复习无） */
  showSm2Info?: boolean;
  /** 是否展示复习次数/间隔（Review 有） */
  showReviewMeta?: boolean;
  /** 填空题是否自动聚焦 */
  fillAutoFocus?: boolean;
  /** 头部额外信息（如统计） */
  headerExtra?: ReactNode;
  /** 是否最后一题（决定"下一题/完成"文案） */
  isLast: boolean;
  /** 下一题按钮文案（默认 下一题/完成复习） */
  nextButtonText?: string;
  /** 本次是否已提交自评（提交后禁用四档按钮，防重复自评） */
  selfRated?: boolean;
  /** 是否正在提交自评 */
  selfRatingSubmitting?: boolean;
  /**
   * 语义判分开关的当前值（阶段 3.5，仅简答题有意义）
   *
   * ⚠️ 状态**必须由页面持有**，不能藏在本组件里：回车提交的处理函数在页面上
   * （`onKeyDown` → `handleSubmit`），若开关只在卡片内部，用户勾了框再按回车
   * 就会静默按"不判分"提交 —— "选了但没生效"是最难被发现的一类不一致。
   */
  semanticGrading?: boolean;
  onToggleSemanticGrading?: (enabled: boolean) => void;
  onSelectAnswer: (answer: string) => void;
  onSubmit: () => void;
  /** 用户点击四档自评之一；quality 为 SM-2 分值 0/3/4/5 */
  onSelfRate?: (quality: number) => void;
  /** 跳过自评（逃生口，见 hooks/useSelfRating.ts 的 skipRating 说明） */
  onSkipSelfRate?: () => void;
  onNext: () => void;
  /**
   * 上一题（可选）
   *
   * ## 为什么是可选而不是必填
   *
   * 卡片复习页（`CardReview`）是"翻卡片"的心智模型，没有"第几题"的概念，
   * 传了反而奇怪。三个刷题页（答题复习 / 快速复习 / 今日学习）传它。
   *
   * ⚠️ 不传 `onPrev` 时**不渲染**按钮，而不是渲染一个禁用按钮 ——
   * 后者会让卡片复习页平白多出一个永远点不动的控件。
   */
  onPrev?: () => void;
  /** 是否存在上一题（第一题时为 false，按钮禁用） */
  hasPrev?: boolean;
  /**
   * 今日额度是否已用尽（想法 8 阶段 4）
   *
   * 为真时禁用「提交答案」并给出明确提示。**为什么要有它**：
   * `/review/due` 一次装载 50 道，而每日只能结算 10 道 —— 没有这个 prop 时，
   * 用户做到第 11 道点提交才拿到 429「今日已完成 10 道题」，
   * 而页面上还剩 40 道**看起来完全能点**的题，一道道撞墙。
   *
   * ⚠️ 只影响**未提交**的题：已提交的题本来就没有提交按钮，
   * 回看它们不受额度约束（那是本功能的另一半诉求）。
   *
   * 快速复习**不传**它（那条入口 `skip_daily_limit=True`，不受额度约束）。
   */
  quotaExhausted?: boolean;
}

export default function QuizAnswerCard({
  quiz,
  userAnswer,
  submitted,
  result,
  submitting = false,
  showSm2Info = true,
  showReviewMeta = false,
  fillAutoFocus = false,
  headerExtra,
  isLast,
  nextButtonText,
  selfRated = false,
  selfRatingSubmitting = false,
  semanticGrading = false,
  onToggleSemanticGrading,
  onSelectAnswer,
  onSubmit,
  onSelfRate,
  onSkipSelfRate,
  onNext,
  onPrev,
  hasPrev = false,
  quotaExhausted = false,
}: QuizAnswerCardProps) {
  const { t } = useTranslation();
  const { questionTypeLabels, difficultyLabels, gradingMethodLabels, ratingLabels, verdictLabels } =
    useLabels();
  const labelOfSelfRating = useSelfRatingLabel();

  // 解析选择题选项
  let options: string[] = [];
  if (Array.isArray(quiz.options)) {
    options = quiz.options;
  } else if (typeof quiz.options === 'string' && quiz.options) {
    try {
      const parsed = JSON.parse(quiz.options);
      options = Array.isArray(parsed) ? parsed : [];
    } catch {
      options = [];
    }
  }

  /**
   * 判断某个选项是不是正确答案
   *
   * ## 为什么不能只做一次字符串相等
   *
   * 真库里的 `answer` **有两种形态**（实测 3358 道选择题：**2212 条是单个字母**
   * `"A"`、**1142 条是选项原文** `"A. 发电厂和变电站的建设…"`、4 条其它），
   * 而选项文本一律是 `"A. 发电厂和变电站的建设…"` 这种"字母 + 点 + 内容"。
   *
   * 所以只比全文会漏掉那 2212 条（`"A" !== "A. …"`），
   * 只比首字母又会在选项不以字母开头时误判。三条判据按可靠性从高到低：
   *
   * 1. 全文相等 —— 最可靠；
   * 2. 选项以"答案 + 分隔符"开头（`A` → `A. …` / `A、…` / `A …`）；
   * 3. 答案以"选项首字母 + 分隔符"开头（反向，兜住格式略有出入的情况）。
   *
   * 拿不准时返回 false（**不标**），而不是猜 —— 标错"正确答案"比不标更糟。
   */
  function isCorrectOption(opt: string, correct: string | undefined | null): boolean {
    const a = (correct ?? '').trim();
    if (!a) return false;
    if (opt.trim() === a) return true;
    // `A` → `A. …`：要求后面紧跟分隔符，避免 `A` 命中 `AB. …`
    const head = new RegExp(`^${a.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[.、．,，:：)）\\s]`);
    if (head.test(opt.trim())) return true;
    const first = opt.trim().charAt(0);
    if (/[A-Za-z]/.test(first)) {
      const back = new RegExp(`^${first}[.、．,，:：)）\\s]`);
      if (back.test(a)) return true;
    }
    return false;
  }

  const typeLabel = questionTypeLabels[quiz.question_type] || quiz.question_type;
  const diffLabel = difficultyLabels[quiz.difficulty || ''] || quiz.difficulty || '';

  // 是否仍需要用户自评：后端明确要求，且本次会话尚未给出自评。
  // 注意不要用 result.is_correct 之类推断——简答题的占位判分恒为"错误"。
  const needsSelfAssessment = !!result?.needs_self_assessment && !selfRated;

  // 语义判分明细：null 表示"本次没有判分"，与"判分过但没发现问题"是两回事
  const detail = result?.grading_detail ?? null;
  const missingPoints = detail?.missing_points ?? [];
  const misconceptions = detail?.misconceptions ?? [];
  const verdictMeta = detail ? verdictLabels[detail.verdict] : undefined;
  const verdictLabel = verdictMeta?.label ?? t('quizCard.graded');
  const verdictColor = verdictMeta?.color ?? 'var(--color-primary)';

  const ratingLabel = result?.sm2?.rating ? ratingLabels[result.sm2.rating] : '';
  // 首次复习的 predicted_retention 恒为 1（"从未复习过，必然想得起来"），
  // 显示成"预测还能想起 100%"没有信息量，反而像在敷衍。只在 <1 时展示。
  const rawRetention = result?.sm2?.predicted_retention;
  const showRetention = typeof rawRetention === 'number' && rawRetention < 0.999;

  return (
    <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
      {/* 题目头部 */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-md)',
        }}
      >
        <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
          <span
            style={{
              padding: '2px 8px',
              borderRadius: 4,
              fontSize: '0.8rem',
              background: 'var(--color-primary)',
              color: '#fff',
            }}
          >
            {typeLabel}
          </span>
          {diffLabel && (
            <span
              style={{
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: '0.8rem',
                background: difficultyColors[quiz.difficulty || ''] || '#999',
                color: '#fff',
              }}
            >
              {diffLabel}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
          {showReviewMeta && quiz.review_count != null && quiz.review_count > 0 && (
            <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
              {t('quizCard.reviewStats', {
                count: quiz.review_count,
                interval: quiz.interval ?? 0,
              })}
            </span>
          )}
          {headerExtra}
        </div>
      </div>

      {/* 题目内容 */}
      <p style={{ fontSize: '1.1rem', lineHeight: 1.6, marginBottom: 'var(--space-md)' }}>
        {quiz.question}
      </p>

      {/* 答题区域 */}
      {!submitted ? (
        <>
          {/* 选择题 */}
          {quiz.question_type === 'choice' && options.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
              {options.map((opt, i) => (
                <button
                  key={i}
                  className={`${styles.quizOption}${userAnswer === opt ? ` ${styles.quizOptionSelected}` : ''}`}
                  onClick={() => onSelectAnswer(opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
          )}

          {/* 填空题 */}
          {quiz.question_type === 'fill_blank' && (
            <input
              type="text"
              value={userAnswer}
              onChange={(e) => onSelectAnswer(e.target.value)}
              placeholder={t('quizCard.answerPlaceholder')}
              autoFocus={fillAutoFocus}
              style={{
                width: '100%',
                padding: 'var(--space-sm) var(--space-md)',
                border: '1px solid var(--color-border)',
                borderRadius: 6,
                fontSize: '1rem',
              }}
            />
          )}

          {/* 简答题 */}
          {quiz.question_type === 'short_answer' && (
            <textarea
              value={userAnswer}
              onChange={(e) => onSelectAnswer(e.target.value)}
              placeholder={t('quizCard.shortAnswerPlaceholder')}
              rows={4}
              style={{
                width: '100%',
                padding: 'var(--space-sm) var(--space-md)',
                border: '1px solid var(--color-border)',
                borderRadius: 6,
                fontSize: '1rem',
                resize: 'vertical',
              }}
            />
          )}

          {/* 语义判分开关（阶段 3.5 的前端入口；仅简答题有意义）
              默认关闭的理由见后端 SubmitAnswerRequest.use_semantic_grading：
              判分在提交的同步路径上调外部 LLM，会给每次提交叠加一次往返延迟，
              而两阶段流程本来就以用户自评为主评分来源。
              这里把选择权交给用户，而不是替他决定"慢一点但更准"。 */}
          {onToggleSemanticGrading && quiz.question_type === 'short_answer' && (
            <label
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 'var(--space-xs)',
                marginTop: 'var(--space-sm)',
                fontSize: '0.85rem',
                color: 'var(--color-text-secondary)',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={semanticGrading}
                onChange={(e) => onToggleSemanticGrading(e.target.checked)}
                disabled={submitting}
                style={{ marginTop: 3 }}
              />
              <span>
                {t('quizCard.aiGradeLabel')}
                <br />
                <span style={{ fontSize: '0.8rem' }}>{t('quizCard.aiGradeHint')}</span>
              </span>
            </label>
          )}

          {/* 提交按钮（submitting 时禁用，防连点重复提交，见 docs/decisions.md#F-23；
              额度用尽时也禁用并说明原因，见 `quotaExhausted` 的说明） */}
          <div style={{ marginTop: 'var(--space-md)', textAlign: 'right' }}>
            <button
              className="btn btn-primary"
              onClick={onSubmit}
              disabled={!userAnswer.trim() || submitting || quotaExhausted}
              title={quotaExhausted ? t('quizNav.quotaExhaustedHint') : undefined}
            >
              {t('quizCard.submit')}
            </button>
            {quotaExhausted && (
              <p
                style={{
                  marginTop: 'var(--space-xs)',
                  fontSize: 'var(--text-sm)',
                  color: 'var(--color-warning)',
                }}
              >
                {t('quizNav.quotaExhaustedHint')}
              </p>
            )}
          </div>

          {/* 上一题（未提交时也要有）
              ⚠️ 这里曾经没有 —— 整行导航都写在 `submitted` 分支里，于是
              **答题前根本翻不动页**：想跳过一道不会做的题、或回头看看
              上一题，都必须先提交。而"这道题我不会、先看别的"是刷题里
              最常见的动作之一，强制提交等于逼用户乱填一个答案。

              只给「上一题」不给「下一题」：往后走由"提交答案"承担，
              留着它才能维持"答完才推进"的主线；往回走不该有任何门槛。 */}
          {onPrev && (
            <div style={{ marginTop: 'var(--space-sm)' }}>
              <button
                className="btn btn-secondary"
                onClick={onPrev}
                disabled={!hasPrev}
                title={hasPrev ? undefined : t('quizNav.firstQuestion')}
              >
                {t('quizNav.prevQuestion')}
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          {/* 选择题的选项（**已提交后也要显示**）
              ⚠️ 这一段曾经不存在 —— 选项只在未提交分支渲染，所以**答完一道
              选择题，选项就消失了**（刷新后回看同样没有）。而"我当时选的哪个、
              正确的哪个"恰恰是选择题复习的全部内容：只给一个
              "回答错误 + 正确答案: A. …" 的结论，用户没法对照其余选项。

              三条信息都要给：**正确项**、**用户错选项**、以及其余项的原样文本。
              标记用"符号 + 文字 + 颜色"三通道，不只靠颜色（WCAG 1.4.1）。

              刻意**不做成禁用按钮**：禁用元素对读屏不友好（不进 Tab 序列、
              状态难播报），而且这里本来就没有"可点"的语义 —— 用纯文本 + 标记。 */}
          {quiz.question_type === 'choice' && options.length > 0 && (
            <ul className={styles.optionReview}>
              {options.map((opt, i) => {
                const isCorrect = isCorrectOption(opt, result?.correct_answer);
                const isPicked = userAnswer === opt;
                const cls = isCorrect
                  ? styles.optionReviewCorrect
                  : isPicked
                    ? styles.optionReviewWrong
                    : '';
                const mark = isCorrect
                  ? t('quizNav.optionCorrect')
                  : isPicked
                    ? t('quizNav.optionYourWrong')
                    : '';
                return (
                  <li key={i} className={`${styles.optionReviewItem} ${cls}`.trim()}>
                    <span className={styles.optionReviewText}>{opt}</span>
                    {mark && (
                      <span className={styles.optionReviewMark}>
                        <span aria-hidden="true">{isCorrect ? '✓' : '✗'}</span> {mark}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {/* 判分结果
              等待自评时（needs_self_assessment）**不显示对错**：此时后端给的
              quality 只是占位值（简答题固定 1 = "错误"），照常渲染会把每个
              还没自评的简答题都标成"回答错误"，是明确的误导。

              ⚠️ 下面两处"回答正确 / 回答错误"的文字色原本是字面量
              `#4caf50` / `#f44336`（白底 2.78:1 / 3.68:1，都不到 4.5:1），
              已改成 `--color-success` / `--color-error` 两个令牌（5.93:1 / 5.17:1）。
              换令牌而不是换字面量：同一语义在 `base.css` 里已经有取值，
              两处各写一份必然漂移（a11y-audit 的 F-19 → F-33 就是这么来的）。 */}
          {needsSelfAssessment ? (
            <div
              style={{
                marginBottom: 'var(--space-md)',
                padding: 'var(--space-sm) var(--space-md)',
                background: 'var(--color-bg)',
                borderLeft: '3px solid var(--color-primary)',
                borderRadius: 4,
              }}
            >
              <p style={{ fontWeight: 600 }}>{t('quizCard.selfRatingPrompt')}</p>
              <p
                style={{
                  marginTop: 'var(--space-xs)',
                  fontSize: '0.9rem',
                  color: 'var(--color-text-secondary)',
                }}
              >
                {t('quizCard.shortAnswerNote')}
              </p>
            </div>
          ) : (
            <div
              className={result?.is_correct ? styles.feedbackCorrect : styles.feedbackIncorrect}
              style={{ marginBottom: 'var(--space-md)' }}
            >
              <p
                style={{
                  fontWeight: 600,
                  color: result?.is_correct ? 'var(--color-success)' : 'var(--color-error)',
                }}
              >
                {result?.is_correct ? t('quizCard.correct') : t('quizCard.incorrect')}
              </p>
              {!result?.is_correct && (
                <p style={{ marginTop: 'var(--space-xs)' }}>
                  <strong>{t('quizCard.correctAnswer')}</strong> {result?.correct_answer}
                </p>
              )}
            </div>
          )}

          {/* 参考答案：等待自评时必须可见，否则用户无从对照打分 */}
          {needsSelfAssessment && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <p>
                <strong>{t('quizCard.referenceAnswer')}</strong> {result?.correct_answer}
              </p>
              {result?.explanation && (
                <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}>
                  <strong>{t('quizCard.explanation')}</strong> {result.explanation}
                </p>
              )}
            </div>
          )}

          {/* 语义判分明细（阶段 3.5 的落地处）
              这里展示的是"缺了哪一点 / 误解了哪一点"，而不是一个分数 ——
              分数无法校准、也没有指导价值。`grading_detail` 为 null 表示
              本次**没有**判分（未请求/判分失败/已自评），此时整块不渲染；
              绝不能渲染成"没有发现问题"。 */}
          {detail && (
            <div
              style={{
                marginBottom: 'var(--space-md)',
                padding: 'var(--space-sm) var(--space-md)',
                background: 'var(--color-bg)',
                borderLeft: `3px solid ${verdictColor}`,
                borderRadius: 4,
                fontSize: '0.9rem',
              }}
            >
              <p style={{ fontWeight: 600, color: verdictColor }}>
                {t('quizCard.aiVerdict', { verdict: verdictLabel })}
              </p>
              {detail.reason && <p style={{ marginTop: 'var(--space-xs)' }}>{detail.reason}</p>}
              {missingPoints.length > 0 && (
                // `<ul>` **不能是 `<p>` 的后代**：HTML 规范里 `<p>` 只收短语内容。
                // React 用 `createElement` 建树，会**照建**这棵非法 DOM、只在 DEV
                // 打一条 `validateDOMNesting` 警告 —— 所以它此前一直没被发现
                // （而真被 HTML 解析器处理时，`<ul>` 会被"顶"出 `<p>` 之外，
                // 结构就变了）。换成 `<div>`：div 装列表是合法的。
                // 显式补回 `<p>` 原本的 1em 下外边距 —— 全局样式表没有重置
                // `p` 的 margin，不补就会少 14.4px（此处 font-size 是 0.9rem）。
                <div style={{ marginTop: 'var(--space-xs)', marginBottom: '1em' }}>
                  <strong>{t('quizCard.omissions')}</strong>
                  <ul style={{ margin: '4px 0 0 1.2em', padding: 0 }}>
                    {missingPoints.map((point, i) => (
                      <li key={i}>{point}</li>
                    ))}
                  </ul>
                </div>
              )}
              {misconceptions.length > 0 && (
                // 同上：`<ul>` 不能住进 `<p>` 里
                <div style={{ marginTop: 'var(--space-xs)', marginBottom: '1em' }}>
                  <strong>{t('quizCard.misunderstandings')}</strong>
                  <ul style={{ margin: '4px 0 0 1.2em', padding: 0 }}>
                    {misconceptions.map((point, i) => (
                      <li key={i}>{point}</li>
                    ))}
                  </ul>
                </div>
              )}
              {missingPoints.length === 0 && misconceptions.length === 0 && (
                <p style={{ marginTop: 'var(--space-xs)', color: 'var(--color-text-secondary)' }}>
                  {t('quizCard.noIssues')}
                </p>
              )}
            </div>
          )}

          {/* 原文语境（阶段 3.13）
              答错之后用户最想做的就是回原文看一眼，而改造前这里只有
              「正确答案」四个字。
              **不在 `needsSelfAssessment` 时隐藏**：等待自评时用户正在
              对照参考答案回忆，原文段落恰恰是最有用的对照材料。 */}
          <SourceContext cardId={quiz.card_id} noteId={quiz.note_id} />

          {/* 四档自评：仅在后端明确请求自评且本次尚未自评时展示。
              控件本身与卡片复习页共用（SelfRatingButtons），差异只有
              "跳过自评"这个逃生口：答题复习有（这里是两阶段提交的第二阶段，
              库中已有占位记录），卡片复习没有（那里的自评就是提交本身）。 */}
          {needsSelfAssessment && (
            <SelfRatingButtons
              onRate={(quality) => onSelfRate?.(quality)}
              submitting={selfRatingSubmitting}
              onSkip={onSkipSelfRate}
            />
          )}

          {/* 自评结果确认：让用户看到自己的打分确实生效了。
              批次 E6 加了一枚**印章**（`seal` 图标 + 一次盖章动效）。
              用墨印而不是绿勾是有意的：绿勾在这个项目里表示"客观判对了"，
              而自评是主观的、由用户自己盖的章 —— 用金色印章正好落在
              `visual-design-spec` 的 R2「金色是印章，不是装饰」上。
              动效尊重 `prefers-reduced-motion`（见模块文件末尾）。 */}
          {selfRated && result?.self_rating != null && (
            <div className={styles.selfRatingDone}>
              <span className={styles.selfRatingSeal} aria-hidden="true">
                <Icon name="seal" size={20} />
              </span>
              {t('quizCard.selfRatingRecorded', { rating: labelOfSelfRating(result.self_rating) })}
            </div>
          )}

          {/* 调度依据（阶段 3.6 / 3.9）
              改造前这里写着 `下次复习: 6 天后 | EF: 2.5 | 评分: 4` ——
              `EF` 是 SM-2 的旋钮，用户看不懂，而且换 FSRS 之后它已经
              不再参与调度（现在由难度 D 桥接而来）。改为展示真正决定
              间隔的两个量：**复习前预测的回忆概率**与**评分档位**。 */}
          {showSm2Info && result?.sm2 && !needsSelfAssessment && (
            <div
              style={{
                fontSize: '0.85rem',
                color: 'var(--color-text-secondary)',
                padding: 'var(--space-sm)',
                background: 'var(--color-bg)',
                borderRadius: 4,
                marginBottom: 'var(--space-md)',
              }}
            >
              {t('quizCard.nextReview', { interval: result.sm2.interval, quality: result.quality })}
              {ratingLabel && <>{t('quizCard.ratingTier', { label: ratingLabel })}</>}
              {showRetention && (
                <>
                  {' | '}
                  {t('quizCard.predictedRetention', {
                    percent: Math.round((result.sm2.predicted_retention ?? 0) * 100),
                  })}
                </>
              )}
              {result.grading_method && (
                <>
                  {' '}
                  {t('quizCard.gradingMethod', {
                    method: gradingMethodLabels[result.grading_method] ?? result.grading_method,
                  })}
                </>
              )}
            </div>
          )}

          {/* 上一题 / 下一题
              下一题在等待自评时禁用：此刻调度尚未推进，直接进入下一题会让这道题
              永远停在未完成状态（见 docs/overhaul-plan.md 阶段 1.4）。

              ⚠️ 上一题**不**受 `needsSelfAssessment` 约束，这是刻意的：
              自评是"给这道题结账"，而往回翻是"去看别处"，两者无关。
              把上一题也锁住会让用户卡在一道不想现在评的题上 ——
              而"往后翻不受限、往前翻也不该受限"才是对称的。

              按钮行从 `textAlign: right` 改成 `space-between`：只有两个按钮
              都渲染时才有布局意义；只渲染下一题时右对齐的观感与原来一致。 */}
          <div
            style={{
              display: 'flex',
              justifyContent: onPrev ? 'space-between' : 'flex-end',
              alignItems: 'center',
              gap: 'var(--space-sm)',
            }}
          >
            {onPrev && (
              <button
                className="btn btn-secondary"
                onClick={onPrev}
                disabled={!hasPrev}
                title={hasPrev ? undefined : t('quizNav.firstQuestion')}
              >
                {t('quizNav.prevQuestion')}
              </button>
            )}
            <button
              className="btn btn-primary"
              onClick={onNext}
              disabled={needsSelfAssessment}
              title={needsSelfAssessment ? t('quizCard.needSelfRating') : undefined}
            >
              {nextButtonText ?? (isLast ? t('quizCard.finishReview') : t('quizCard.nextQuestion'))}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
