/**
 * @file 统计卡片（仪表盘 / 今日学习共用）
 * @description 展示一个统计数字 + 一行说明，顶部 3px 色条由 `variant` 决定。
 *
 * 它是 `overhaul-plan 5.6` 第三批的产物：`.stat-card*` / `.stat-number` /
 * `.stat-label` 原本是全局类名，被 `Dashboard.tsx` 与 `TodayLearn.tsx`
 * 以完全相同的 DOM 结构使用，因此无法整组搬进任一页面的模块
 * （详见 `StatCard.module.css` 文件头）。抽成组件后样式随组件进模块。
 *
 * 这是**纯抽取**：类名语义、DOM 层级（`div > div + div`）、文本内容
 * 与每个声明值都和迁移前逐字一致；两个页面的调用点只是把原来的三段 JSX
 * 换成一次 `<StatCard />`。
 */
import type { ReactNode } from 'react';
import styles from './StatCard.module.css';

/** 顶部色条的配色变体（对应 `.statCardBlue/Green/Gold/Purple`） */
export type StatCardVariant = 'blue' | 'green' | 'gold' | 'purple';

/**
 * 变体 → 模块类名的**显式查表**。
 *
 * ⚠️ 不能写成 `styles[`statCard${variant}`]` 之类的动态取值：
 *   1. 测试环境下 `.module.css` 的默认导出是 Proxy，`Object.keys(styles)`
 *      是空数组（`css-convention.md` §6 实测定论），凡是要枚举的地方都会
 *      静默得到"什么都没有"；
 *   2. 显式查表能让 `tsc` 在漏掉一个变体时报错（与 `DiffView.tsx` 的
 *      `LINE_TYPE_CLASS` 同一写法）。
 */
const VARIANT_CLASS: Record<StatCardVariant, string> = {
  blue: styles.statCardBlue,
  green: styles.statCardGreen,
  gold: styles.statCardGold,
  purple: styles.statCardPurple,
};

interface StatCardProps {
  /** 顶部色条配色 */
  variant: StatCardVariant;
  /** 主数字 / 主文案（原 `.stat-number` 的内容） */
  value: ReactNode;
  /** 数字下方的一行说明（原 `.stat-label` 的内容） */
  label: string;
}

export default function StatCard({ variant, value, label }: StatCardProps) {
  return (
    <div className={`${styles.statCard} ${VARIANT_CLASS[variant]}`}>
      <div className={styles.statNumber}>{value}</div>
      <div className={styles.statLabel}>{label}</div>
    </div>
  );
}
