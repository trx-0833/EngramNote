/**
 * @file 掌握度环形指示（知识卡片列表 / 卡片详情共用）
 *
 * ## 为什么把"进度条"换成环（visual-refactor-plan 批次 E3）
 *
 * 借鉴来源是 `docs/visual-symbol-research.md` §C3 的「知识卡片」行与 §C2 第 6 行：
 * 掌握度用**环形**（或 1–5 段小圆点）表达，**不用星星**。
 * 知识卡片列表里原先是一条 6px 的横向进度条 —— 进度条的语义是"还差多少做完"，
 * 而掌握度是一个**可以回落**的刻度（遗忘会让它掉下来），用环读起来更像"刻度"
 * 而不是"完成度"；环也不占满整行宽度，卡片网格里少一条横线。
 *
 * ## 色阶必须与"难度"同源（`KnowledgeCards.tsx` 文件头那条既有约定）
 *
 * 颜色**只有一个来源**：`utils/labels.ts` 的 `getMasteryColor`，而它与
 * `difficultyColors` 是逐值对齐的（A4 收敛过：`#c0392b / #8f7020 / #25714a`，
 * 三档都在白底上达标）。组件把它的返回值写在**行内 `color`** 上，
 * SVG 侧只写 `stroke: currentColor` —— 换色只需改一处，不会出现
 * "环一种绿、难度徽章另一种绿"。
 *
 * ## 无障碍：环是装饰，数值在文字里
 *
 * 环本身 `aria-hidden`（它的信息与右侧可见文字**完全重复**，读屏用户听两遍
 * 只会更吵）；真正承载数值的是 `掌握度 NN%` 这一句可见文字，所以
 * 读屏用户**不会**丢掉掌握度信息（对照 `Icon.tsx` 的 `title` 约定：
 * 装饰性图形不传可访问名）。
 *
 * ## 几何（不是图标，不套 24 网格）
 *
 * 它不是 `Icon`：`viewBox` 用 36×36 的自定义坐标系（Lucide 那套 24 网格 + 2px
 * 留白是为**线性图标**定的，环需要的是"描边半径 + 线宽"两个量算得开）。
 * 半径 15.5 + 线宽 3.5，外径 17.25 < 18，描边不会溢出画布。
 */
import { getMasteryColor } from '../utils/labels';
import styles from './MasteryRing.module.css';

/** 环的半径（viewBox 单位） */
const RING_RADIUS = 15.5;
/**
 * 周长 = 2πr。`stroke-dasharray` 取整周长、`stroke-dashoffset` 按百分比回退，
 * 得到的就是一段"按比例生长"的弧 —— 不需要 path，也不需要 JS 画图。
 */
const RING_LENGTH = 2 * Math.PI * RING_RADIUS;

interface MasteryRingProps {
  /** 掌握度（0–100）；超出范围的输入会被夹到边界，不会画出一个越界的环 */
  level: number;
}

export default function MasteryRing({ level }: MasteryRingProps) {
  // 夹取放在**上色之前**：色阶与弧长必须看到同一个数，
  // 否则 level = -3 时会出现"红色 + 空环"这种自相矛盾的绘制。
  const clamped = Math.min(100, Math.max(0, level));

  return (
    <span className={styles.masteryRing}>
      <svg
        className={styles.masteryRingSvg}
        style={{ color: getMasteryColor(clamped) }}
        viewBox="0 0 36 36"
        width={28}
        height={28}
        aria-hidden="true"
        focusable="false"
      >
        <circle className={styles.masteryRingTrack} cx="18" cy="18" r={RING_RADIUS} />
        {/* `rotate(-90 18 18)`：弧从 12 点方向开始长，与钟表读数一致。
            用 SVG 属性而不是 CSS `transform` —— 属性的坐标系就是 viewBox，
            不必再依赖 `transform-box` 在各浏览器上的默认值。 */}
        <circle
          className={styles.masteryRingValue}
          cx="18"
          cy="18"
          r={RING_RADIUS}
          transform="rotate(-90 18 18)"
          strokeDasharray={RING_LENGTH}
          strokeDashoffset={RING_LENGTH * (1 - clamped / 100)}
        />
      </svg>
      <span className={styles.masteryRingText}>掌握度 {Math.round(clamped)}%</span>
    </span>
  );
}
