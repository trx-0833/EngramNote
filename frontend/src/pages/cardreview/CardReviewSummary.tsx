/**
 * @file 卡片复习的本轮汇总
 *
 * 单独成模块是为了让 `CardReview.tsx` 专注于会话状态机（谁在到期队列里、
 * 当前这张到哪一阶段、怎么推进）；这里的数字口径值得单独盯：
 *
 * - `sessionCount` 只数**本次会话**自评过的张数，不是库里的复习次数；
 * - `sessionPassed` 是自评 >= 3（"想起来了"/"轻松想起"）的张数 ——
 *   卡片没有自动判分，`is_correct` 正是后端按同一口径折算的；
 * - `loadedCount < totalDue` 时必须提示"还有更多"：真库 1183 张全到期，
 *   而接口一次只给 20 张，不提示用户会以为清空了（附录 AA.5）。
 */
interface CardReviewSummaryProps {
  /** 本次会话已自评的张数 */
  sessionCount: number;
  /** 其中"想起来了"（自评 >= 3）的张数 */
  sessionPassed: number;
  /** 本次加载到的张数 */
  loadedCount: number;
  /** 真正到期的总数（后端计数，不是本页条数） */
  totalDue: number;
  /** 再复习一轮（重新拉取到期队列） */
  onRestart: () => void;
  /** 回到今日学习 */
  onBack: () => void;
}

export default function CardReviewSummary({
  sessionCount,
  sessionPassed,
  loadedCount,
  totalDue,
  onRestart,
  onBack,
}: CardReviewSummaryProps) {
  return (
    <div className="page-enter" style={{ maxWidth: 600, margin: '0 auto' }}>
      <h2 style={{ marginBottom: 'var(--space-lg)' }}>本轮卡片复习完成</h2>
      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        <h3 style={{ marginBottom: 'var(--space-sm)' }}>本次统计</h3>
        <p>复习卡片: {sessionCount} 张</p>
        <p>想起来了: {sessionPassed} 张</p>
        <p
          style={{
            color: 'var(--color-text-secondary)',
            fontSize: '0.9rem',
            marginTop: 'var(--space-sm)',
          }}
        >
          {totalDue > loadedCount
            ? `本次加载了 ${loadedCount} 张，全部到期共 ${totalDue} 张 —— 可以再来一轮。`
            : '到期队列已经清空。'}
        </p>
      </div>
      <div style={{ display: 'flex', gap: 'var(--space-sm)' }}>
        <button className="btn btn-primary" onClick={onRestart}>
          再复习一轮
        </button>
        <button className="btn btn-secondary" onClick={onBack}>
          回到今日学习
        </button>
      </div>
    </div>
  );
}
