/**
 * @file 复习会话进度条（5.12：两条复习流程的统一件）
 *
 * ## 为什么要有它
 *
 * 四个复习界面（答题复习 / 快速复习 / 今日学习 / 卡片复习）此前各写了一遍
 * 同样的进度条，而其中最容易写错、也最难被发现的是那条宽度公式：
 * `(当前序号 + 已结账 ? 1 : 0) / 总数`。它重复四遍意味着"某页少算了刚结账的
 * 那一张"这类偏差不会有人发现 —— 用户只看到进度条涨得慢一点。
 * 现在公式与标记只有一份，页面只负责提供文案。
 *
 * ## 为什么保留两种排版
 *
 * - 答题复习侧：页面标题在别处（或没有），进度条与两侧计数排在同一行；
 * - 卡片复习侧：这一块自带页面标题（"卡片复习"），所以是"标题行 + 全宽条"。
 *
 * 这不是随手写出来的差异 —— 把卡片页也压成一行会让它丢掉页面标题。
 * 差异由 `title` 是否存在表达，两种排版共用同一条进度条与同一个公式。
 *
 * ## 关于 `total = 0`
 *
 * 页面在无内容时都走空状态，不会渲染到这里。仍然兜一层：0 作除数会得到
 * `NaN%`，那会渲染成一条"宽度无效"的进度条 —— 比"0% 进度"糟得多，
 * 因为它看起来像渲染坏了。
 */
import type { ReactNode } from 'react';

interface ReviewProgressProps {
  /** 当前项序号（0 起） */
  index: number;
  /** 本次会话的项数 */
  total: number;
  /** 当前项是否已结账（已提交 / 已自评）—— 已结账的这一张要计入进度 */
  done: boolean;
  /** 计数文案：无标题时在进度条左侧，有标题时在标题行右侧 */
  label: ReactNode;
  /** 进度条右侧的补充计数（正确数 / 今日额度）；只有一行式排版有这个位置 */
  trailing?: ReactNode;
  /** 页面标题：给了就切到"标题行 + 全宽条"排版（见文件头） */
  title?: ReactNode;
}

/** 计数文案的统一样式（两侧计数此前是两处手写、值相同的内联样式） */
const counterStyle = { fontSize: '0.9rem', color: 'var(--color-text-secondary)' } as const;

export default function ReviewProgress({
  index,
  total,
  done,
  label,
  trailing,
  title,
}: ReviewProgressProps) {
  const percent = total > 0 ? ((index + (done ? 1 : 0)) / total) * 100 : 0;

  // 块级父元素下 `flex: 1` 无效果（全宽条）；flex 行里则占满剩余宽度（一行式）。
  // 一份标记同时服务两种排版，避免又出现两条会各自漂移的进度条。
  const bar = (
    <div className="progress-bar" style={{ flex: 1 }}>
      <div className="progress-bar-fill" style={{ width: `${percent}%` }} />
    </div>
  );

  if (title !== undefined) {
    return (
      <div style={{ marginBottom: 'var(--space-md)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
          {/* `h1` 而不是 `h2`：`title` 存在就代表"这一块自带页面标题"
              （本组件文件头里的两种排版），而当前唯一的调用方是卡片复习页
              —— 那一页除此之外没有任何标题，axe 判 `page-has-heading-one`
              （F-14/F-15）。改成 h1 之后它也成了页面上唯一的 h1，
              顺带消掉"h2 出现在 h1 之前"的层级隐患。 */}
          <h1 style={{ fontSize: '1.2rem' }}>{title}</h1>
          <span style={counterStyle}>{label}</span>
        </div>
        {bar}
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-sm)',
        marginBottom: 'var(--space-lg)',
      }}
    >
      <span style={counterStyle}>{label}</span>
      {bar}
      {trailing && <span style={counterStyle}>{trailing}</span>}
    </div>
  );
}
