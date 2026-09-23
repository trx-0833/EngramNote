/**
 * @file 页面标题（visual-refactor-plan 批次 C1）
 * @description 一个页面的 `<h1>` + 可选副标题 + 可选右侧动作区。
 *
 * ## 为什么要有它
 *
 * 批次 0.1 的现状探针（`e2e/shot-probe.spec.ts`）量出 18 个页面各自内联写
 * `<h1 style={{ fontSize: … }}>`，一共**五档字号**：`2rem`（Dashboard /
 * LearningGoals）、`1.75rem`（LearningAssessment / ProjectsHeader，靠
 * `.assessment-title` 这个全局类）、`1.5rem`（12 个页面）、`1.25rem`
 * （NotesList）、`1.2rem` 且**不带衬线**（卡片复习页，全站唯一脱离标题体系的一处）。
 * 字号之外还有三处不一致：字距、字重、以及"谁负责换行"。
 *
 * 本批把它们统一到**一处**：`--text-xl`(1.5rem) + `--font-serif` + 600 +
 * `letter-spacing: 0`，见 `PageHeader.module.css`。
 *
 * ## 统一到 1.5rem 是**有意**的影响面（不要"修正"回去）
 *
 * | 页面 | 改前 | 改后 |
 * |---|---|---|
 * | Dashboard | 2rem | 1.5rem（变小） |
 * | LearningGoals | 2rem | 1.5rem（变小） |
 * | NotesList | 1.25rem | 1.5rem（变大） |
 * | LearningAssessment | 1.75rem | 1.5rem（变小） |
 * | ProjectsHeader | 1.75rem | 1.5rem（变小） |
 * | 卡片复习（`ReviewProgress` 的 `title`） | 1.2rem 无衬线 | 1.5rem 衬线 |
 * | 其余 12 个页面 | 1.5rem | 1.5rem（**观感不变**） |
 *
 * ## 为什么有 `spacing`
 *
 * 各页面页头"与下方内容隔多远"此前是**各写各的**：10 处 `--space-lg`，
 * 另有 `--space-sm`（Dashboard / NotesList）、`--space-md`（QuestionSets）、
 * `--space-xl`（两个 `.assessment-header` 页面）与"不隔"（图谱工具栏、
 * 卡片复习的标题行）。本批的验收要求"其余 12 个页面的观感保持不变"，
 * 所以间距必须**逐页可指定**，而不是让组件拍一个默认值把所有页面都挪一遍。
 * 取值用显式查表（同 `StatCard.tsx` 的 `VARIANT_CLASS`）：
 * 写错一个档位 `tsc` 会当场报错，动态拼类名则会在哈希后静默失效。
 *
 * ## 为什么 `title` 必须是字符串（而不是 `ReactNode`）
 *
 * 它渲染成 `<h1>`：既是这一页在大纲里的名字，也是 `page-has-heading-one`
 * 与各页面 e2e 定位式的锚点。允许传节点会让"这一页叫什么"无法从 props 上
 * 看出来，而标题恰好是最不该靠运行时才能确定的一处。
 * 需要富文本的地方（副标题、动作区）另有 `subtitle` / `actions` 两个口子。
 */
import type { ReactNode } from 'react';
import styles from './PageHeader.module.css';

/**
 * 页头与下方内容的间距档位（令牌名一一对应：`xs` = `--space-xs`）。
 *
 * 默认 `lg`：18 个页面里 10 个原本就是这一档。
 */
export type PageHeaderSpacing = 'none' | 'xs' | 'sm' | 'md' | 'lg' | 'xl';

/**
 * 档位 → 模块类名的**显式查表**。
 *
 * ⚠️ 不能写成 `styles[\`pageHeaderSpace${spacing}\`]`：测试环境下
 * `.module.css` 的默认导出是 Proxy，`Object.keys(styles)` 是空数组
 * （`docs/css-convention.md` §6 实测定论），动态取值拿不到东西。
 * 显式查表还能让 `tsc` 在漏掉一个档位时报错。
 */
const SPACING_CLASS: Record<PageHeaderSpacing, string> = {
  none: styles.pageHeaderSpaceNone,
  xs: styles.pageHeaderSpaceXs,
  sm: styles.pageHeaderSpaceSm,
  md: styles.pageHeaderSpaceMd,
  lg: styles.pageHeaderSpaceLg,
  xl: styles.pageHeaderSpaceXl,
};

interface PageHeaderProps {
  /** 页面名（渲染成 `<h1>`） */
  title: string;
  /** 标题下方的一行说明（可选）。它与标题之间的间距由模块固定为 `--space-xs` */
  subtitle?: ReactNode;
  /** 右侧动作区（按钮 / 计数 / 按钮组）。给了就与标题块同一行两端对齐 */
  actions?: ReactNode;
  /** 与下方内容的间距档位，默认 `lg`（见 `PageHeaderSpacing`） */
  spacing?: PageHeaderSpacing;
}

export default function PageHeader({ title, subtitle, actions, spacing = 'lg' }: PageHeaderProps) {
  return (
    <div className={`${styles.pageHeader} ${SPACING_CLASS[spacing]}`}>
      {/* 标题块始终多包一层：副标题必须落在标题**下方**，
          而它同时还要让动作区能贴到整块的最右边。没有副标题时这一层
          也不省 —— 条件 DOM 会让"同一个页面在有没有副标题时结构不同"，
          测试与样式都要各写两遍。 */}
      <div className={styles.pageHeaderText}>
        <h1 className={styles.pageHeaderTitle}>{title}</h1>
        {subtitle !== undefined && <p className={styles.pageHeaderSubtitle}>{subtitle}</p>}
      </div>
      {actions !== undefined && <div className={styles.pageHeaderActions}>{actions}</div>}
    </div>
  );
}
