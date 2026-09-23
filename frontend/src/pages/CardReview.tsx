/**
 * @file 卡片复习页（阶段 3.12 的前端一半）
 *
 * ## 为什么需要这一页
 *
 * 后端早就提供了 `GET /review/cards/due` 与 `POST /review/cards/{id}/submit`
 * —— 目的是解决 overhaul-plan 症状 L-5：**没有生成过题目的卡片永远无法复习**
 * （调度参数原先只挂在 `quiz_items` 上）。但这在前端**没有任何调用方**，
 * 于是"卡片可以直接复习"这件事从做完到现在，用户一次也没法用。
 *
 * 这一页补的就是那一步。它和答题复习是两个独立入口，不是同一条流程的两种皮肤。
 *
 * ## 交互：先回忆 → 再翻面 → 才自评
 *
 * 卡片正文**默认隐藏**。这不是装饰：卡片复习的全部价值在于"先自己想一遍"，
 * 一开始就把内容摊开等于直接看答案，用户会把它当成快速浏览而不是回忆练习
 * —— 那样产生的自评数据是假的，而它**会真的改变调度**（间隔一经写入就
 * 无法事后纠正）。
 *
 * ## 与答题复习统一到什么程度（5.12）
 *
 * 两条流程共用同一套**交互件**，而不是各写一份长得像的东西：
 *
 * | 交互件 | 位置 | 两条流程的关系 |
 * |---|---|---|
 * | 四档自评控件 | `components/quiz/SelfRatingButtons` | 完全相同（含"提交中禁用"反馈） |
 * | 会话进度条 | `components/quiz/ReviewProgress` | 同一个公式与标记，排版差异用 `title` 表达 |
 * | 回车键约定 | `components/quiz/useReviewKeyboard` | 相同（含"按钮目标放行"守卫），焦点策略可配 |
 * | 原文语境 | `components/quiz/SourceContext` | 相同（懒加载 + 失败可见 + 跳回原文） |
 *
 * 刻意**不**统一的部分，以及各自的理由：
 *
 * - **评分来源**：答题复习可能由后端自动判分（选择/填空），自评只补简答题的
 *   占位记录；卡片没有可判分的答案，自评是唯一评分来源。所以这里没有
 *   "跳过自评"逃生口（`SelfRatingButtons` 的 `onSkip`）：跳过等于什么都没提交。
 * - **提交路径**：答题走 `useSelfRating`（两阶段：占位 → 带自评补完），
 *   卡片走单阶段的 `submitCardReview`（quality 必填）。两者的入参与响应类型
 *   都不同，强行合并只会把"两阶段"这层语义藏进类型体操里。
 * - **调度依据面板**：这里展示 S / D 与掌握度变化，答题侧展示间隔 / 档位 /
 *   判分方式 —— 字段集合不同，见 `cardreview/ScheduleFeedback.tsx` 的说明。
 */
import { useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import SourceContext from '../components/quiz/SourceContext';
import SelfRatingButtons from '../components/quiz/SelfRatingButtons';
import ReviewProgress from '../components/quiz/ReviewProgress';
import { useReviewKeyboard } from '../components/quiz/useReviewKeyboard';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import ScheduleFeedback from './cardreview/ScheduleFeedback';
import CardReviewSummary from './cardreview/CardReviewSummary';
import CardFace from './cardreview/CardFace';
import { useCardReviewSession } from './cardreview/useCardReviewSession';

export default function CardReview() {
  const navigate = useNavigate();
  const {
    loading,
    error,
    cards,
    totalDue,
    currentIndex,
    submitting,
    completed,
    sessionCount,
    sessionPassed,
    current,
    phase,
    reload,
    restart,
    handleReveal,
    handleRate,
    handleNext,
  } = useCardReviewSession();

  /** 回车 = 推进当前这一步：未翻面则翻面，已自评则下一张（与答题复习同一约定） */
  const handleEnter = useCallback(() => {
    if (!current) return;
    if (current.result) handleNext();
    else if (!current.revealed) handleReveal();
  }, [current, handleNext, handleReveal]);

  // 这一页没有任何输入控件，焦点必须由容器自己接住：点完按钮焦点落到 body 后，
  // 键盘事件再也冒泡不到容器，回车键会"时灵时不灵"（附录 AA.7）。见 hook 的说明。
  const { containerRef, handleKeyDown } = useReviewKeyboard({
    onEnter: handleEnter,
    refocusKey: `${currentIndex}:${phase}`,
  });

  // --- 加载中 ---
  if (loading) return <LoadingSpinner text="加载到期卡片..." />;

  // --- 错误 ---
  if (error && cards.length === 0) return <ErrorDisplay message={error} onRetry={reload} />;

  // --- 无到期卡片 ---
  if (!loading && cards.length === 0) {
    return (
      <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
        <EmptyState
          message="没有到期的卡片"
          description={
            '卡片复习只包含「已进入复习计划」的卡片。' +
            '刚导入的卡片会在你第一次复习它们时加入计划；' +
            '若刚重建过数据，可能需要先运行一次复习状态迁移。'
          }
          action={
            <button className="btn btn-secondary" onClick={() => navigate('/cards')}>
              去看知识卡片
            </button>
          }
        />
      </div>
    );
  }

  // --- 本次完成汇总 ---
  if (completed) {
    return (
      <CardReviewSummary
        sessionCount={sessionCount}
        sessionPassed={sessionPassed}
        loadedCount={cards.length}
        totalDue={totalDue}
        onRestart={restart}
        onBack={() => navigate('/today')}
      />
    );
  }

  if (!current) return null;

  const { card, revealed, result } = current;

  return (
    <div
      className="page-enter"
      ref={containerRef}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
      style={{ maxWidth: 760, margin: '0 auto', outline: 'none' }}
    >
      {/* 进度与到期总量 */}
      <ReviewProgress
        index={currentIndex}
        total={cards.length}
        done={!!result}
        title="卡片复习"
        label={
          <>
            第 {currentIndex + 1} / {cards.length} 张
            {totalDue > cards.length && <> · 到期共 {totalDue} 张</>}
          </>
        }
      />

      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        {/* 正面 = 标题（提示）；背面 = 正文 + 摘要。先回忆再翻面是这一页的全部意义 */}
        <CardFace card={card} revealed={revealed} onReveal={handleReveal} />

        {revealed && (
          <>
            {/* 四档自评：**唯一**的评分来源（卡片没有可自动判分的答案）。
                控件与答题复习页共用；这里不传 onSkip —— 卡片复习是单阶段提交，
                自评就是提交本身，"跳过"等于什么都没提交（见组件文件头）。 */}
            {!result && (
              <SelfRatingButtons
                prompt="刚才想得起来吗？"
                submitting={submitting}
                onRate={(quality) => void handleRate(quality)}
              />
            )}

            {/* 调度依据（阶段 3.6 / 3.9）：把"凭什么排到 N 天后"摆出来 */}
            {result && <ScheduleFeedback result={result} previousMastery={card.mastery_level} />}

            {/* 原文语境（阶段 3.13）：想不起来时最该做的事就是回原文 */}
            {result && <SourceContext cardId={card.card_id} noteId={card.note_id} />}

            <div style={{ textAlign: 'right' }}>
              <button
                className="btn btn-primary"
                onClick={handleNext}
                disabled={!result}
                title={!result ? '请先完成自评' : undefined}
              >
                {currentIndex >= cards.length - 1 ? '完成本轮' : '下一张'}
              </button>
            </div>
          </>
        )}
      </div>

      <p style={{ fontSize: '0.85rem', color: 'var(--color-text-secondary)', textAlign: 'center' }}>
        卡片复习是轻量回顾，不占用每日答题限额。 想复习带题目的内容请到{' '}
        <Link to="/review">答题复习</Link>。
      </p>
    </div>
  );
}
