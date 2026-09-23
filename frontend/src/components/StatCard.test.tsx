/**
 * @file 统计卡片的 DOM 契约（overhaul-plan 5.6 第三批）
 *
 * ## 为什么抽组件这一轮必须有测试
 *
 * `.stat-card` / `.stat-number` / `.stat-label` 原来是**全局类名**，被
 * `Dashboard.tsx` 与 `TodayLearn.tsx` 用同一段 JSX 各写了 4 遍；第三批把它们
 * 抽成 `StatCard` 组件、样式随组件进模块。计划里给这一步加的前置条件是
 * "一次带视觉核对的独立改动" —— 本文件是那件事在无头环境里能做到的替代：
 * 把两个页面依赖的**结构与类名映射**钉死，任何"顺手改一刀"都会红。
 *
 * ## 类名用 `styles.X` 而不是字面量
 *
 * 类名进模块后被哈希（实测定论见 `docs/css-convention.md` §6：测试环境下
 * `.module.css` 的默认导出是 Proxy，`styles.foo` 返回 `_foo_<hash>`）。
 * 写 `.stat-number` 这种字面量在这里必然查不到 —— 与第二批
 * `NoteAskPanel.test.tsx` 的处理一致。
 *
 * ⚠️ 反过来说：**不要**对 `styles` 做枚举（`Object.keys(styles)` 是空数组）。
 * 所以下面判定"4 个变体互不相同"时比较的是渲染出来的字符串，不是键集合。
 */
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import StatCard, { type StatCardVariant } from './StatCard';
import styles from './StatCard.module.css';

/** 4 个变体（顺序无关，但要全覆盖：漏一个就会让那条色条静默变透明） */
const VARIANTS: StatCardVariant[] = ['blue', 'green', 'gold', 'purple'];

describe('StatCard：从 dashboard.css 抽出的统计卡片', () => {
  it('★ 结构与迁移前逐字一致：卡片 > 数字 div + 说明 div', () => {
    const { container } = render(<StatCard variant="blue" value={12} label="新掌握" />);

    const card = container.firstElementChild as HTMLElement;
    expect(card.tagName, '外层必须是 div（迁移前就是 div.stat-card）').toBe('DIV');
    expect(card.className).toContain(styles.statCard);
    expect(card.className).toContain(styles.statCardBlue);
    expect(card.children, '只允许两个子节点：数字 + 说明').toHaveLength(2);

    expect(card.children[0].className).toBe(styles.statNumber);
    expect(card.children[0].textContent).toBe('12');
    expect(card.children[1].className).toBe(styles.statLabel);
    expect(card.children[1].textContent).toBe('新掌握');
  });

  it('★ 4 个变体渲染出 4 个互不相同的类名组合', () => {
    const rendered = VARIANTS.map((variant) => {
      const { container } = render(<StatCard variant={variant} value={1} label="x" />);
      return (container.firstElementChild as HTMLElement).className;
    });

    expect(new Set(rendered).size, '有变体共用了同一个类名：色条会一起变色').toBe(4);
    for (const className of rendered) expect(className).toContain(styles.statCard);
  });

  it('value 支持字符串（正确率带 %、时长是格式化后的文本）', () => {
    const { container } = render(<StatCard variant="gold" value="85%" label="正确率" />);

    expect(container.querySelector(`.${styles.statNumber}`)?.textContent).toBe('85%');
    expect(container.querySelector(`.${styles.statLabel}`)?.textContent).toBe('正确率');
  });
});
