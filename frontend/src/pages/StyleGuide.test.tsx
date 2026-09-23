/**
 * @file 设计样板间的表征测试（visual-refactor-plan 批次 F1）
 *
 * ## 为什么要给一个"只有开发者会打开"的页面写测试
 *
 * 这一页的**全部价值**在于"它和实际设计同步"。它不是文档 —— 文档会腐烂，
 * 而这一页读的是 `:root` 上的**实际令牌值**、列的是 `ICONS` 注册表里的
 * **实际键**。所以它一旦与实现脱节（图标表少一格、清单里留着已经删掉的令牌），
 * 它就从"验收工具"退化成了"另一份会过期的说明"。
 *
 * 下面几条断言钉的正是这种脱节：
 *
 * | 断言 | 脱节的表现 |
 * |---|---|
 * | 46 个语义名逐一出现在表里 | 新增图标没进图标表 —— "缺哪个一眼可见"变成"缺了也看不见" |
 * | 没有"未归组"那一段 | 新增图标忘了加进 `ICON_GROUPS` |
 * | 清单里**没有** `--shadow-focus` | 把一条已经做出的决定（D4：它不该存在）伪装成待修的漂移 |
 * | 四个按钮变体都在 | 矩阵漏了变体 |
 *
 * ## 关于 `import.meta.env.DEV`
 *
 * 生产构建里 `StyleGuide` 根本不注册（`App.tsx` 的守卫），但**组件本身**可以直接
 * 渲染 —— 这一层测试与那个守卫无关，它测的是"渲染出来对不对"。
 * "生产产物不含它"由 `App.tsx` 的守卫与构建产物保证，不是本文件的事。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ICON_NAMES } from '../components/Icon';
import StyleGuide from './StyleGuide';

describe('StyleGuide · 图标表', () => {
  it('★ 注册表里的每个语义名都在表里（少一格就等于图标表说了谎）', () => {
    render(<StyleGuide />);

    ICON_NAMES.forEach((name) => {
      // 只匹配 <code>：名字如 today / error 也是普通英文词，
      // 不加 selector 会在说明文字里误命中
      expect(screen.getByText(name, { selector: 'code' })).toBeInTheDocument();
    });
  });

  it('★ 没有"未归组"那一段（新增图标忘了进 ICON_GROUPS 就会红）', () => {
    render(<StyleGuide />);

    expect(screen.queryByText(/未归组/)).not.toBeInTheDocument();
  });

  it('标题上的图标总数与实际注册表一致', () => {
    render(<StyleGuide />);

    expect(screen.getByText(new RegExp(`全 ${ICON_NAMES.length} 个`))).toBeInTheDocument();
  });
});

describe('StyleGuide · 令牌清单与实际令牌层不脱节', () => {
  it('★ 不再登记批次 D4 删掉的 --shadow-focus', () => {
    render(<StyleGuide />);

    // 它当初是作为"待落地"列进来的，而 D4 的结论是它**根本不该存在**
    // （box-shadow 会盖掉元素自身阴影、在 forced-colors 下会消失）。
    // 留在清单里只会永远显示"未定义"，把已做出的决定伪装成待修的漂移。
    expect(screen.queryByText('--shadow-focus')).not.toBeInTheDocument();
  });

  it('展示的是真实令牌值，而不是写死的字面量', () => {
    render(<StyleGuide />);

    // jsdom 里 getComputedStyle 读不到 base.css 的值（样式表不参与计算），
    // 所以这里能断言的是"它去读了"：读不到就该显示「未定义」，
    // 而不是把清单里那个 label 当成值渲染出来。
    const undefinedMarks = screen.getAllByText('未定义');
    expect(undefinedMarks.length).toBeGreaterThan(0);
  });
});

describe('StyleGuide · 组件状态矩阵', () => {
  it('四个按钮变体都在，且每个都有禁用态', () => {
    render(<StyleGuide />);

    ['btn-primary', 'btn-secondary', 'btn-ghost', 'btn-danger'].forEach((cls) => {
      expect(screen.getByText(`.${cls}`)).toBeInTheDocument();
    });

    // 每个变体渲染两枚按钮（默认 + disabled）→ 4 × 2 = 8
    const buttons = screen.getAllByRole('button', { name: '按钮' });
    expect(buttons).toHaveLength(8);
    expect(buttons.filter((b) => b.hasAttribute('disabled'))).toHaveLength(4);
  });

  it('对话框演示的入口在页面上（点开才验证得了焦点陷阱那五件事）', () => {
    render(<StyleGuide />);

    expect(screen.getByRole('button', { name: '打开对话框' })).toBeInTheDocument();
  });
});
