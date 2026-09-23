/**
 * @file 页面标题的 DOM 契约（visual-refactor-plan 批次 C1）
 *
 * ## 为什么一个新组件要有测试
 *
 * 这一批把 18 个页面的标题从"各自内联写死的 `<h1 style={{fontSize}}>`"
 * 换成同一个组件。而那 18 个页面里，有 8 个是靠**页面级 e2e 定位式**
 * （`getByRole('heading', { name: '…' })`）确认"页面渲染出来了"的
 * —— 标题一旦不再是 `<h1>`、或文本被包进别的节点，那些用例会一起红，
 * 而红的表象是"页面没渲染出来"，与真实原因（标题结构变了）毫无关系。
 *
 * 所以这里钉三件事：
 *   1. 根节点永远是 `<h1>`，文本就是 `title` 本身（不多套一层 span）；
 *   2. `actions` / `subtitle` 不出现时**不渲染空容器**（空 div 会改变
 *      `space-between` 的两端对齐结果，也会让"标题块 + 动作区"的
 *      子节点数随页面漂移）；
 *   3. 6 个 spacing 档位是 6 个**互不相同**的类名（查表写错一个键，
 *      页头与下方内容的间距会静默退化成 0）。
 *
 * ## 类名用 `styles.X` 而不是字面量
 *
 * 类名进模块后被哈希（实测定论见 `docs/css-convention.md` §6：测试环境下
 * `.module.css` 的默认导出是 Proxy，`styles.foo` 返回 `_foo_<hash>`）。
 * 写 `.page-header` 这类字面量在这里必然查不到。
 *
 * ⚠️ 反过来说：**不要**对 `styles` 做枚举（`Object.keys(styles)` 是空数组），
 * 所以下面判定"6 档互不相同"时比较的是渲染出来的字符串，不是键集合。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import PageHeader, { type PageHeaderSpacing } from './PageHeader';
import styles from './PageHeader.module.css';

/** 6 个档位（顺序无关，但要全覆盖：漏一个就会让那个页面的页头不再隔开内容） */
const SPACINGS: PageHeaderSpacing[] = ['none', 'xs', 'sm', 'md', 'lg', 'xl'];

describe('PageHeader：页面标题的唯一量尺', () => {
  it('★ 渲染的就是一个 <h1>，文本恰是 title（e2e 的 getByRole 靠这一条）', () => {
    const { container } = render(<PageHeader title="笔记" />);

    const root = container.firstElementChild as HTMLElement;
    expect(root.tagName, '根节点是 div（页头那一行）').toBe('DIV');
    expect(root.className).toContain(styles.pageHeader);

    // `getByRole('heading', { name: '笔记' })` 正是各页面 e2e 的写法
    const heading = screen.getByRole('heading', { name: '笔记' });
    expect(heading.tagName, '页面标题必须是 h1（axe: page-has-heading-one）').toBe('H1');
    expect(heading.className).toBe(styles.pageHeaderTitle);
    expect(heading.textContent).toBe('笔记');
  });

  it('★ actions 给了才渲染动作区，且与标题块并列为根的两个子节点', () => {
    const { container, unmount } = render(<PageHeader title="笔记" />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.children, '没有 actions 时不许有空容器').toHaveLength(1);

    unmount();

    const withActions = render(
      <PageHeader title="学习目标" actions={<button type="button">新建目标</button>} />,
    );
    const rootWithActions = withActions.container.firstElementChild as HTMLElement;
    expect(rootWithActions.children).toHaveLength(2);
    expect(rootWithActions.children[1].className).toBe(styles.pageHeaderActions);
    expect(screen.getByRole('button', { name: '新建目标' })).toBeInTheDocument();
  });

  it('subtitle 渲染在标题**下方**（同一个标题块里），没有就不渲染', () => {
    const { container, unmount } = render(<PageHeader title="学习评估" />);
    expect(container.querySelector(`.${styles.pageHeaderSubtitle}`)).toBeNull();

    unmount();

    const withSubtitle = render(
      <PageHeader title="学习评估" subtitle="通过笔记比对或开放性问题，评估你的掌握程度" />,
    );
    const text = withSubtitle.container.querySelector(`.${styles.pageHeaderText}`) as HTMLElement;
    expect(text.children, '标题块 = h1 + p，顺序不能反').toHaveLength(2);
    expect(text.children[0].tagName).toBe('H1');
    expect(text.children[1].tagName).toBe('P');
    expect(text.children[1].textContent).toBe('通过笔记比对或开放性问题，评估你的掌握程度');
  });

  it('★ 6 个 spacing 档位渲染出 6 个互不相同的类名（查表漏键会让间距静默变 0）', () => {
    const rendered = SPACINGS.map((spacing) => {
      const { container } = render(<PageHeader title="x" spacing={spacing} />);
      return (container.firstElementChild as HTMLElement).className;
    });

    expect(new Set(rendered).size, '有两个档位共用了同一个类名：间距会一起变').toBe(
      SPACINGS.length,
    );
    for (const className of rendered) expect(className).toContain(styles.pageHeader);
  });

  it('spacing 的默认值是 lg（18 个页面里 10 个原本就是这一档）', () => {
    const { container: byDefault } = render(<PageHeader title="x" />);
    const { container: explicit } = render(<PageHeader title="x" spacing="lg" />);

    expect((byDefault.firstElementChild as HTMLElement).className).toBe(
      (explicit.firstElementChild as HTMLElement).className,
    );
    expect((byDefault.firstElementChild as HTMLElement).className).toContain(
      styles.pageHeaderSpaceLg,
    );
  });
});
