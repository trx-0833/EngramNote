/**
 * @file 图标唯一出口（visual-design-spec §4.2 / visual-refactor-plan 批次 B1）
 *
 * ## 为什么要有"唯一出口"
 *
 * 此前全站没有任何图标组件：侧边栏的 13 个"图标"是 `Sidebar.tsx:22-54` 里硬编码的
 * Unicode 码点 —— 跨平台会被渲染成彩色 emoji（`\u2753` ❓、`\u2611` ☑）或彼此难分的
 * 几何符号（▷ ▣ ◉ ◈ ◎）；散在各页面的内联 `<svg>` 又各有各的 viewBox 与线宽
 * （同一骨架 18×18 与 16×16 并存，视觉线宽 1.5 与 1.33 混用）。
 *
 * 这个文件把**几何参数一次性锁死**，让"看起来像不像一套"不再取决于每个调用点：
 *
 * | 参数 | 值 | 依据 |
 * |---|---|---|
 * | `viewBox` | `0 0 24 24` | Lucide / Obsidian 一致（见 §4.1） |
 * | 内容区 | 20×20，四周留 2px | Obsidian 规范：给不同形状留等量呼吸 |
 * | 线宽 | `1.5` | 全站锁死；中文笔画细，2px 会抢戏（本项目判断） |
 * | 端点/拐角 | `round` | Lucide / Obsidian 一致 |
 * | 颜色 | `currentColor` | 继承文字色，**绝不写字面色值** |
 * | 填充 | `none` | 统一线性图标 |
 * | 尺寸 | 只允许 16 / 20 / 24 | 收敛现状里 16 与 18 混用 |
 *
 * 网格与线宽刻意与 Lucide 完全对齐：将来若要换成图标库，可以逐个替换图形，
 * 不需要重排任何布局。
 *
 * ## 用法
 *
 * ```tsx
 * <Icon name="review-cards" />          // 默认 20px；纯装饰（aria-hidden）
 * <Icon name="goals" size={16} />       // 16 / 20 / 24 三档
 * <Icon name="trash" title="回收站" />  // 有语义时：role="img" + aria-label + <title>
 * ```
 *
 * `name` 是**语义名**（"这块内容是什么"）而不是图形名（"画的是什么形状"）：
 * 同一个语义将来换图形，调用点一行都不用改。未知名字在**编译期**报错 ——
 * `IconName` 是 13 个字的联合类型，不是 `string`（见 icons/index.ts）。
 */
import type { SVGProps } from 'react';

import { ICONS, type IconName } from './icons';

export type { IconName };

/**
 * 全部语义名，按注册表里的定义顺序（导航 → 动作 → 状态 → 对象）
 *
 * 给**枚举用途**的地方：设计样板间 `/styleguide` 要把 46 个图标排成表，
 * 新增图标时那张表要自动多一格，而不是靠人手同步两份清单。
 * 业务代码不需要它 —— 业务代码只写死自己用的那一个名字。
 *
 * ⚠️ `Object.keys` 的类型是 `string[]`，这里的断言由 `icons/index.ts` 的
 * `satisfies` 兜住：注册表的键就是 `IconName`，两者不可能分叉。
 */
export const ICON_NAMES = Object.keys(ICONS) as IconName[];

/** 允许的渲染尺寸（px）—— 只此三档，避免再次出现 16/18 混用 */
export type IconSize = 16 | 20 | 24;

/**
 * `name` / `width` / `height` 从透传属性里摘掉：
 * 后两者由 `size` 唯一决定（否则会出现 `size={20}` 却渲染成 40 的写法），
 * `name` 是语义名，不能与原生 SVG 的 `name` 属性混用。
 */
export interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name' | 'width' | 'height'> {
  /** 语义名（联合类型：拼错在编译期就报错，不会漏到运行期） */
  name: IconName;
  /** 渲染边长，默认 20 */
  size?: IconSize;
  /**
   * 图标的可读名称。**只在图标承载了文字之外的语义时才传**。
   * 传了 → 渲染 `<title>` 并加 `role="img"` + `aria-label`；
   * 不传 → 默认 `aria-hidden="true"`（纯装饰，读屏跳过）。
   */
  title?: string;
}

export default function Icon({ name, size = 20, title, ...rest }: IconProps) {
  const Glyph = ICONS[name];
  const labelled = title !== undefined;

  return (
    <svg
      // className / style / data-* / 事件原样透传。下面几项是组件契约，写在透传之后：
      // B1 的硬约束是"全站统一"，调用点可以挂类名，但不能把线宽、网格或无障碍属性改歪。
      {...rest}
      // 装饰性图标紧挨着文字，必须让读屏跳过它（否则"仪表盘"会被念两遍）；
      // 有 title 时反过来：role="img" + aria-label 让它成为一个有名字的图像。
      {...(labelled ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true })}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* <title> 必须是 svg 的第一个孩子，读屏才按它取名字 */}
      {labelled && <title>{title}</title>}
      <Glyph />
    </svg>
  );
}
