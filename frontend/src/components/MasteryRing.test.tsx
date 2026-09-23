/**
 * @file 掌握度环形的测试（visual-refactor-plan 批次 E3）
 *
 * 这一批把知识卡片列表里的**进度条**换成了环。换形状本身没法断言（jsdom 不布局），
 * 但这次改动里**有语义的三件事**都能钉住：
 *
 * 1. **色阶同源**：环的颜色必须与"难度"（`difficultyColors`）逐值一致 ——
 *    这正是 `KnowledgeCards.tsx` 文件头那条 A4 收敛过的约定。
 *    这里不比较"看起来是什么颜色"，而是直接比"算出来的值"，
 *    所以哪天有人给环另写一套绿，这条会红。
 * 2. **弧长与色阶看同一个数**：越界的掌握度先夹取再上色/画弧，
 *    否则 `-3` 会画出"红环 + 空弧"这种自相矛盾的图形。
 * 3. **环是装饰件、数值在文字里**：环 `aria-hidden`，可见文字仍是
 *    `掌握度 NN%` —— 读屏用户不会因为这次改版丢掉掌握度信息。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import MasteryRing from './MasteryRing';
import { difficultyColors, getMasteryColor } from '../utils/labels';

/** 同一个色值在 jsdom 里的规范写法（`#8f7020` 会被读成 `rgb(143, 112, 32)`） */
function normalizeColor(value: string): string {
  const probe = document.createElement('span');
  probe.style.color = value;
  return probe.style.color;
}

/** 环上那条"值弧"（第二条 circle）的 dasharray / dashoffset */
function readArc(container: HTMLElement): { dash: number; offset: number } {
  const circles = container.querySelectorAll('circle');
  expect(circles.length, '环应当由"底槽 + 值弧"两条 circle 组成').toBe(2);
  const value = circles[1];
  return {
    dash: Number(value.getAttribute('stroke-dasharray')),
    offset: Number(value.getAttribute('stroke-dashoffset')),
  };
}

describe('MasteryRing：数值表达', () => {
  it('★ 可见文字承载数值（读屏用户不靠图形读掌握度）', () => {
    render(<MasteryRing level={62.4} />);
    expect(screen.getByText('掌握度 62%')).toBeInTheDocument();
  });

  it('★ 环本身是装饰件：aria-hidden，不会把同一件事播报两遍', () => {
    const { container } = render(<MasteryRing level={62.4} />);
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg!.getAttribute('aria-hidden')).toBe('true');
    // 没有可访问名（装饰件不该有 role=img / <title>），
    // 与 Icon.tsx "不传 title 就是 aria-hidden" 的约定同形
    expect(svg!.getAttribute('role')).toBeNull();
    expect(svg!.querySelector('title')).toBeNull();
  });

  it('弧长按百分比：dashoffset / 周长 = 1 - level/100', () => {
    for (const level of [0, 25, 62.4, 100]) {
      const { container, unmount } = render(<MasteryRing level={level} />);
      const { dash, offset } = readArc(container);
      expect(dash, '周长应当是一个正数（2πr）').toBeGreaterThan(0);
      expect(offset / dash).toBeCloseTo(1 - level / 100, 5);
      unmount();
    }
  });

  it('越界值先夹取再画：-5 与 150 都不会画出越界的弧', () => {
    const low = render(<MasteryRing level={-5} />);
    expect(readArc(low.container).offset).toBeCloseTo(readArc(low.container).dash, 5);
    low.unmount();

    const high = render(<MasteryRing level={150} />);
    expect(readArc(high.container).offset).toBeCloseTo(0, 5);
    expect(screen.getByText('掌握度 100%')).toBeInTheDocument();
  });
});

describe('MasteryRing：色阶与"难度"同源（KnowledgeCards 文件头的既有约定）', () => {
  it('★ 每个档位的环色都等于 getMasteryColor 的返回值', () => {
    for (const level of [10, 55, 95]) {
      const { container, unmount } = render(<MasteryRing level={level} />);
      const svg = container.querySelector('svg') as SVGElement;
      expect(normalizeColor(svg.style.color)).toBe(normalizeColor(getMasteryColor(level)));
      unmount();
    }
  });

  it('★ 三档色值逐值等于 difficultyColors（红 / 金 / 绿一套色阶）', () => {
    // 与 utils/labels.ts 的 A4 收敛口径一致：难度与掌握度是**同一种程度语义**
    expect(getMasteryColor(10)).toBe(difficultyColors.hard);
    expect(getMasteryColor(55)).toBe(difficultyColors.medium);
    expect(getMasteryColor(95)).toBe(difficultyColors.easy);

    for (const [level, expected] of [
      [10, difficultyColors.hard],
      [55, difficultyColors.medium],
      [95, difficultyColors.easy],
    ] as const) {
      const { container, unmount } = render(<MasteryRing level={level} />);
      const svg = container.querySelector('svg') as SVGElement;
      expect(normalizeColor(svg.style.color)).toBe(normalizeColor(expected));
      unmount();
    }
  });

  it('夹取发生在上色之前：越界值的颜色取自夹取后的那个数', () => {
    const { container } = render(<MasteryRing level={-20} />);
    const svg = container.querySelector('svg') as SVGElement;
    expect(normalizeColor(svg.style.color)).toBe(normalizeColor(getMasteryColor(0)));
  });
});

describe('MasteryRing：样式表这一侧也是单一颜色来源', () => {
  /**
   * 为什么读磁盘上的 `.module.css` 而不是查 DOM：
   * 本项目 `vite.config.ts` 没开 `test.css`，Vitest 会把 `.css` 导入变成空串
   * （`mobile-input-font-size.test.ts` 文件头记了这条实测），
   * 要断言"弧的 stroke 写的是什么"只能亲自读文件。
   * `node:fs` 的类型就地声明 —— 本项目没装 `@types/node`
   * （同样是 `mobile-input-font-size.test.ts` 的先例）。
   */
  interface NodeFs {
    readFileSync(path: string, encoding: 'utf8'): string;
  }

  async function readModuleCss(): Promise<string> {
    // `expect.getState().testPath` 在**模块作用域**还是 undefined，
    // 所以路径必须等到用例里再算（算错会拼出一条指向根目录的假路径）
    const testPath = expect.getState().testPath;
    if (!testPath) throw new Error('expect.getState().testPath 为空：定位不到测试文件所在目录');
    const cssPath = `${testPath.replace(/[/\\][^/\\]*$/, '')}/MasteryRing.module.css`;

    const fs = await vi.importActual<NodeFs>('node:fs');
    const text = fs.readFileSync(cssPath, 'utf8');
    // 自检：读到空文件说明路径算错了，而不是"文件里没问题"
    expect(text.length, `读不到 ${cssPath}`).toBeGreaterThan(0);
    return text.replace(/\/\*[\s\S]*?\*\//g, '');
  }

  it('★ 值弧用 currentColor 上色（环节点把 getMasteryColor 写在行内 color 上）', async () => {
    const css = await readModuleCss();
    const rule = /\.masteryRingValue\s*\{([^}]*)\}/.exec(css);
    expect(rule, '取不到 .masteryRingValue 这条规则：护栏会失明').not.toBeNull();
    expect(rule![1]).toMatch(/stroke\s*:\s*currentColor/);
  });

  it('★ 模块里没有任何硬编码色值（颜色只走令牌或 currentColor）', async () => {
    const css = await readModuleCss();
    const hits = [...css.matchAll(/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\s*\(/g)].map((m) => m[0]);
    expect(hits, '新写的颜色必须走 base.css 的令牌（design-drift 会数这个）').toEqual([]);
  });
});
