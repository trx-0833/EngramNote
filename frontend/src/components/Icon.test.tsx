/**
 * @file 图标唯一出口的契约测试（visual-refactor-plan 批次 B1）
 *
 * ## 这一页在钉什么
 *
 * `Icon.tsx` 是 B1 建立的唯一出口，B2/B3 会把全站的 Unicode / emoji / 内联 SVG 都换到
 * 它上面。换的过程里最容易出的不是"画得难看"，而是**静默退化**：
 *
 * - 语义名少登记一个 → 侧边栏某一行变成空白，而编译期不会报错（没人引用就不报错）；
 * - `size` 没落到 `width`/`height` → 图标按 CSS 默认尺寸撑成一大块；
 * - 无障碍属性没生效 → 读屏把装饰图标当图像念一遍，或把有语义的图标念不出名字；
 * - `className` 没透传 → 侧边栏的对齐样式失联（B2 正是靠它把 20px 图标与 12.8px 文字对齐）；
 * - 有人"顺手"加一个 `strokeWidth={2}` 或写死颜色的图标 → 全站线宽/配色出现分叉。
 *
 * ## 为什么这里**可以**断言几何参数
 *
 * jsdom 不加载样式表、不做布局，"图标看起来对不对（留白、对齐、粗细观感）"只能靠真机截图。
 * 但 `viewBox` / `stroke-width` / `stroke` / `fill` / 端点样式是**写在 SVG 属性上的**，
 * 属于 DOM 事实，而不是渲染结果 —— 这几条正是 visual-design-spec §4.1 锁死的硬约束，
 * 值得用一条用例钉住，免得半年后有人加进一个"只有自己看得懂"的图标。
 *
 * ## 为什么用 `container.querySelector('svg')` 而不是 `getByRole`
 *
 * 默认情况下图标是**装饰性**的（`aria-hidden="true"`），按定义就不在无障碍树里 ——
 * 用 `getByRole` 去查它本身就是错的（查不到才是对的）。所以这一页用容器查询，
 * 只在"传了 title"的那条用例里才走 `getByRole('img')`（那时它确实应该被查到）。
 */
import type { ReactElement } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import Icon, { type IconName } from './Icon';
import { ICONS } from './icons';

/**
 * 全部语义名 —— **照 visual-design-spec §4.3 的表格手抄**，不从 `ICONS` 推导。
 * 从实现推导的话，"某个名字被删掉"这件事会连同断言一起消失，测试永远绿。
 * 标注 `IconName[]` 还有一层作用：抄错字（如 `review-card`）在这一行就是编译错误。
 *
 * 前 13 个是侧边栏导航（§4.3 的表格）；`logout` 与 `menu` 是**批次 B2** 为
 * 「退出」与「移动端汉堡」新增的两个 —— 它们不在导航表格里，但同样必须登记，
 * 否则下面那条"注册表与清单严格一一对应"会红（那条断言正是为了这件事故意的）。
 *
 * **批次 B3** 又从 15 个补到 46 个，分两段：
 * - `upload` … `seal`：任务表点名的 17 个（动作 / 状态 / 内容）；
 * - `chevron` … `relation`：17 个之外**必须补**的 14 个 —— B3 的替换清单点了名
 *   （`▶` `⋯` `⭐` `🖱` `📖` `+` `−` `⤢` `↔`、认证页三枚内联 SVG、创建模式 `●`、
 *   `Toast` 的提示槽），却没有任何一个现成图形可用。理由逐个写在 `icons/index.ts`
 *   那段注释与各图形文件的文件头里。
 */
const ICON_NAMES: IconName[] = [
  'dashboard',
  'today',
  'review-cards',
  'daily',
  'projects',
  'assessment',
  'goals',
  'notes',
  'trash',
  'cards',
  'graph',
  'qa',
  'questions',
  'logout',
  'menu',
  // ── 批次 B3（一）：任务表点名的 17 个 ──
  'upload',
  'delete',
  'edit',
  'search',
  'add',
  'close',
  'filter',
  'due',
  'processing',
  'ai',
  'success',
  'warning',
  'error',
  'quote',
  'file',
  'folder',
  'seal',
  // ── 批次 B3（二）：17 个之外必须补的 14 个 ──
  'chevron',
  'more',
  'star',
  'mouse',
  'book',
  'zoom-in',
  'zoom-out',
  'fit-screen',
  'mail',
  'lock',
  'user',
  'dot',
  'info',
  'relation',
];

/** 取渲染结果里的 `<svg>` 根（装饰性图标不在无障碍树里，只能用容器查询） */
function renderIcon(element: ReactElement) {
  const { container } = render(element);
  const svg = container.querySelector('svg');
  if (!svg) throw new Error('没有渲染出 <svg> 根元素');
  return svg;
}

describe('唯一出口：46 个图标都画得出来', () => {
  it('★ 46 个语义名全部能渲染出非空 <svg>', () => {
    for (const name of ICON_NAMES) {
      const { container, unmount } = render(<Icon name={name} />);
      const svg = container.querySelector('svg');
      expect(svg, `语义名 ${name} 没有渲染出 <svg>`).not.toBeNull();
      // 只有外壳、没有图形（path/rect/circle）等于画了个空气 —— 名字对但图形忘了接
      expect(svg?.children.length, `语义名 ${name} 的图形是空的`).toBeGreaterThan(0);
      unmount();
    }
  });

  it('★ 注册表与清单严格一一对应（少登记一个 / 多出一个都要红）', () => {
    expect(Object.keys(ICONS).sort()).toEqual([...ICON_NAMES].sort());
  });

  it('★ 图标里不含任何文字（图形只由 path / rect / circle 组成）', () => {
    for (const name of ICON_NAMES) {
      const svg = renderIcon(<Icon name={name} />);
      expect(svg.textContent, `语义名 ${name} 里混进了文字`).toBe('');
    }
  });
});

describe('尺寸：只允许 16 / 20 / 24，默认 20', () => {
  it('★ 三档尺寸分别落到 width / height 属性上', () => {
    for (const size of [16, 20, 24] as const) {
      const svg = renderIcon(<Icon name="dashboard" size={size} />);
      expect(svg.getAttribute('width'), `size=${size} 的 width`).toBe(String(size));
      expect(svg.getAttribute('height'), `size=${size} 的 height`).toBe(String(size));
    }
  });

  it('不传 size 时按默认 20 渲染', () => {
    const svg = renderIcon(<Icon name="dashboard" />);
    expect(svg.getAttribute('width')).toBe('20');
    expect(svg.getAttribute('height')).toBe('20');
  });
});

describe('无障碍：装饰默认隐藏，有语义时按 title 出声', () => {
  it('★ 默认 aria-hidden="true"，且不带 role / <title>（纯装饰）', () => {
    const svg = renderIcon(<Icon name="trash" />);
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).not.toHaveAttribute('role');
    expect(svg.querySelector('title')).toBeNull();
  });

  it('★ 传 title 时变成 role="img" + aria-label，并不再是 aria-hidden', () => {
    const svg = renderIcon(<Icon name="trash" title="回收站" />);
    expect(svg).toHaveAttribute('role', 'img');
    expect(svg).toHaveAttribute('aria-label', '回收站');
    expect(svg).not.toHaveAttribute('aria-hidden');
  });

  it('★ <title> 是 svg 的第一个孩子（读屏按顺序取名字，排在图形后面就读不到）', () => {
    const svg = renderIcon(<Icon name="trash" title="回收站" />);
    expect(svg.firstElementChild?.tagName.toLowerCase()).toBe('title');
  });

  it('★ 可访问名真的取得到（无障碍树里能按名字查到这张图）', () => {
    const svg = renderIcon(<Icon name="goals" title="学习目标" />);
    expect(screen.getByRole('img', { name: '学习目标' })).toBe(svg);
  });
});

describe('属性透传', () => {
  it('★ className / data-* 原样落到 <svg> 上（B2 的对齐样式靠它）', () => {
    const svg = renderIcon(<Icon name="notes" className="sidebar-icon" data-testid="nav-icon" />);
    expect(svg).toHaveClass('sidebar-icon');
    expect(svg).toHaveAttribute('data-testid', 'nav-icon');
  });

  it('透传改不歪硬约束：外部的 width / strokeWidth 覆盖不了 size 与线宽', () => {
    // 只可能来自 JS 变量（TS 里这几个键已被 Omit 掉），这里模拟"绕过类型"的调用方
    const sneaky = { width: 999, strokeWidth: 4 } as unknown as { className: string };
    const svg = renderIcon(<Icon name="notes" size={16} {...sneaky} />);
    expect(svg.getAttribute('width')).toBe('16');
    expect(svg.getAttribute('stroke-width')).toBe('1.5');
  });
});

describe('几何参数：visual-design-spec §4.1 的硬约束逐条钉住', () => {
  it('★ 46 个图标一律 24 网格 / 线宽 1.5 / currentColor / fill:none / round 端点', () => {
    for (const name of ICON_NAMES) {
      const svg = renderIcon(<Icon name={name} />);
      expect(svg.getAttribute('viewBox'), `${name} 的 viewBox`).toBe('0 0 24 24');
      expect(svg.getAttribute('stroke-width'), `${name} 的线宽`).toBe('1.5');
      expect(svg.getAttribute('stroke'), `${name} 的颜色`).toBe('currentColor');
      expect(svg.getAttribute('fill'), `${name} 的填充`).toBe('none');
      expect(svg.getAttribute('stroke-linecap'), `${name} 的端点`).toBe('round');
      expect(svg.getAttribute('stroke-linejoin'), `${name} 的拐角`).toBe('round');
    }
  });

  it('★ 没有任何写死的颜色值（颜色只能来自 currentColor / CSS 变量）', () => {
    for (const name of ICON_NAMES) {
      const { container, unmount } = render(<Icon name={name} />);
      expect(container.innerHTML, `语义名 ${name} 里出现了写死的颜色`).not.toMatch(
        /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i,
      );
      unmount();
    }
  });
});

describe('编译期护栏：未知语义名进不来', () => {
  it('★ name 不是 string，而是 46 个字的联合类型（拼错必须让 tsc 失败）', () => {
    // 这条用例的价值几乎全在**编译期**：@ts-expect-error 在"没有错误"时自己会报错，
    // 所以它同时钉住两件事 —— ① 联合类型没有被放宽成 string；② 名字写错必须报错。
    // 运行期不做渲染：ICONS['not-an-icon'] 是 undefined，渲染必然抛
    // "Element type is invalid"，那是另一回事（这里只想钉住类型）。
    // @ts-expect-error 未知语义名不在 IconName 联合类型里（拼错就该编译失败）
    const unknown: IconName = 'not-an-icon';
    expect(ICONS).not.toHaveProperty(unknown);
  });
});
