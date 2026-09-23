/**
 * @file 四档自评控件的测试（5.12：两条复习流程共用的评分控件）
 *
 * 这个控件是"答题复习"与"卡片复习"唯一**完全一致**的交互件，所以它的契约
 * 必须钉死：档位取值（SM-2 的 0/3/4/5）、提交中禁用、以及**只有答题复习
 * 才有**的跳过逃生口。任何一条漂移都会让两条流程在同一件事上给出不同结果 ——
 * 而自评会真的改变调度（间隔一经写入无法事后纠正）。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { selfRatingOptions } from '../../utils/labels';
import SelfRatingButtons, { selfRatingLabel } from './SelfRatingButtons';

/** 只声明本文件用到的那几个 API（本项目没有 @types/node） */
interface NodeFs {
  readFileSync(path: string, encoding: 'utf8'): string;
}

const fs = await vi.importActual<NodeFs>('node:fs');

/**
 * 本文件所在目录 = `src/components/quiz`。
 *
 * 刻意**不在模块作用域求值**：`expect.getState().testPath` 要等用例开始跑才有值，
 * 模块加载期拿到的是 `undefined` —— 那样路径会拼成 `/SelfRatingButtons.module.css`，
 * 报错信息还会指着一份"看起来没问题"的相对路径。
 *
 * （同 `cardreview/CardFace.test.tsx` / `styles/mobile-input-font-size.test.ts`。
 * 这里**不用** `new URL(..., import.meta.url)`：本项目的 vitest 配置下
 * `import.meta.url` 不是 file: scheme，`readFileSync` 会直接抛
 * `TypeError: The URL must be of scheme file`。）
 */
function dir(): string {
  const testPath = expect.getState().testPath;
  if (!testPath) throw new Error('expect.getState().testPath 为空：定位不到测试文件所在目录');
  return testPath.replace(/[/\\][^/\\]*$/, '');
}

/** 读样式表；读到空内容直接报错，绝不静默放过（护栏失明不是"没问题"） */
function readStylesheet(): string {
  const text = fs.readFileSync(`${dir()}/SelfRatingButtons.module.css`, 'utf8');
  expect(
    text.length,
    '读不到 SelfRatingButtons.module.css：护栏失明，不是"没问题"',
  ).toBeGreaterThan(0);
  return text;
}

describe('SelfRatingButtons 的四档', () => {
  it('四档的标签与说明全部来自 utils/labels（单一数据源，不另写字面量）', () => {
    render(<SelfRatingButtons onRate={vi.fn()} />);

    for (const opt of selfRatingOptions) {
      expect(screen.getByText(opt.label)).toBeInTheDocument();
      expect(screen.getByText(opt.hint)).toBeInTheDocument();
    }
    expect(screen.getAllByRole('button')).toHaveLength(selfRatingOptions.length);
  });

  it.each([0, 3, 4, 5])(
    '★ 点选 quality=%i 的档位要原样上报（SM-2 语义不能被改写）',
    async (quality) => {
      const onRate = vi.fn();
      render(<SelfRatingButtons onRate={onRate} />);
      const opt = selfRatingOptions.find((o) => o.quality === quality);
      if (!opt) throw new Error(`selfRatingOptions 缺少 quality=${quality}`);

      await userEvent.click(screen.getByText(opt.label));

      expect(onRate).toHaveBeenCalledWith(quality);
      expect(onRate).toHaveBeenCalledTimes(1);
    },
  );

  it('上方的提示语只有调用方给了才渲染（卡片复习有，答题复习没有）', () => {
    const { unmount } = render(<SelfRatingButtons onRate={vi.fn()} />);
    expect(screen.queryByText('刚才想得起来吗？')).not.toBeInTheDocument();
    unmount();

    render(<SelfRatingButtons onRate={vi.fn()} prompt="刚才想得起来吗？" />);
    expect(screen.getByText('刚才想得起来吗？')).toBeInTheDocument();
  });
});

describe('SelfRatingButtons 的提交中状态', () => {
  it('★ 提交中禁用全部按钮，并给出"正在记录自评"的反馈', () => {
    render(<SelfRatingButtons onRate={vi.fn()} submitting />);

    for (const opt of selfRatingOptions) {
      expect(screen.getByText(opt.label).closest('button')).toBeDisabled();
    }
    expect(screen.getByText('正在记录自评...')).toBeInTheDocument();
  });

  it('禁用期间点按钮不会上报（防连点重复推进调度）', async () => {
    const onRate = vi.fn();
    render(<SelfRatingButtons onRate={onRate} submitting />);

    await userEvent.click(screen.getByText('想起来了'));

    expect(onRate).not.toHaveBeenCalled();
  });
});

describe('SelfRatingButtons 的跳过逃生口', () => {
  it('★ 传了 onSkip 才出现（答题复习的两阶段提交有占位记录可放弃；卡片复习没有）', async () => {
    const onSkip = vi.fn();
    render(<SelfRatingButtons onRate={vi.fn()} onSkip={onSkip} />);

    await userEvent.click(screen.getByRole('button', { name: '跳过自评，先做下一题' }));

    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it('不传 onSkip 时页面上没有这个入口', () => {
    render(<SelfRatingButtons onRate={vi.fn()} />);
    expect(screen.queryByRole('button', { name: '跳过自评，先做下一题' })).not.toBeInTheDocument();
  });

  it('提交中不显示跳过入口（避免与正在进行的自评抢结果）', () => {
    render(<SelfRatingButtons onRate={vi.fn()} onSkip={vi.fn()} submitting />);
    expect(screen.queryByRole('button', { name: '跳过自评，先做下一题' })).not.toBeInTheDocument();
  });
});

describe('selfRatingLabel', () => {
  it('把 quality 翻译成用户看到的档位名', () => {
    expect(selfRatingLabel(4)).toBe('想起来了');
    expect(selfRatingLabel(0)).toBe('完全忘记');
  });

  it('未知档位回退成 quality=N，而不是 undefined', () => {
    expect(selfRatingLabel(2)).toBe('quality=2');
  });
});

describe('SelfRatingButtons 的布局（批次 E6：四档固定底部横排）', () => {
  /**
   * jsdom **不算布局、也不认 `@media`**，`.css` 导入在 Vitest 里还只是空串 ——
   * 所以这一组读**磁盘上的样式表**做源码级断言，而不是假装在测渲染结果。
   * 先例：`styles/mobile-input-font-size.test.ts`、`cardreview/CardFace.test.tsx`。
   */
  const css = readStylesheet();

  it('★ 四档恒为等宽一横排（不再用 auto-fit）', () => {
    // auto-fit + minmax 的问题是"几档一行"随可用宽度变：1250px 时四档一行，
    // 窄一点就掉成三档、两档。复习时每张卡都要做一次自评，档位位置**跳动**
    // 会直接变成误点。所以这里钉住"恒为 4 列"。
    expect(css).toMatch(/\.selfRatingRow\s*\{[^}]*grid-template-columns:\s*repeat\(4,/);
    expect(css).not.toContain('auto-fit');
  });

  it('★ 整块用 sticky 钉在底部，不是 fixed', () => {
    // sticky 仍占文档流里的空间（滚到底就停在原位，不盖住最后一段内容）；
    // fixed 要永久悬浮，必须给每个调用方的容器补等高的下内边距才不遮东西。
    expect(css).toMatch(/\.selfRatingDock\s*\{[^}]*position:\s*sticky/);
    expect(css).toMatch(/\.selfRatingDock\s*\{[^}]*bottom:\s*0/);
    expect(css).not.toMatch(/\.selfRatingDock\s*\{[^}]*position:\s*fixed/);
    // 不透明底是必须的：否则正文会从按钮的缝隙里透出来，看起来像渲染坏了。
    // ⚠️ 取的是 `--color-surface`（卡片纸白 #ffffff），**不是** `--color-bg`（页面米色 #faf9f7）。
    // 四档标签的颜色是 `utils/labels.ts` 里的字面量、按**白底**调到 4.66:1；
    // 换成米底后同一个 `#8f7020` 只有 4.42:1，跌破 AA 的 4.5:1 ——
    // axe 在 `card-review-back` 场景里报过一次 `color-contrast [serious]`。
    // 两条一起钉：正向确认用了对的令牌，反向拦住"改回米色"。
    expect(css).toMatch(/\.selfRatingDock\s*\{[^}]*background:\s*var\(--color-surface\)/);
    expect(css).not.toMatch(/\.selfRatingDock\s*\{[^}]*background:\s*var\(--color-bg\)/);
  });

  it('两条窄屏规则都还在（56 / 60 的触控目标下限，值一字未改）', () => {
    expect(css).toContain('@media (max-width: 768px)');
    expect(css).toContain('@media (max-width: 480px)');
    expect(css).toContain('min-height: 56px');
    expect(css).toContain('min-height: 60px');
  });

  it('样式表里 0 处硬编码色值（颜色只能来自令牌或 currentColor）', () => {
    // 逐档的档位色刻意留在 tsx（值来自 utils/labels 的单一数据源），
    // 所以样式表里不该出现任何字面色。
    // ⚠️ **先剥注释再数**（本仓库的既有口径）：本项目按惯例在注释里大量引用
    // 色值做说明 —— 比如本文件上面那条"底栏为什么用 --color-surface"就写了
    // 三个 hex。不剥的话这条护栏会因为"注释写得太清楚"而变红，那是假红。
    const code = css.replace(/\/\*[\s\S]*?\*\//g, '');
    expect(code.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([]);
  });
});
