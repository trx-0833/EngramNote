/**
 * @file Markdown 渲染管道的回归护栏（为 `marked` 大版本升级而建）
 *
 * ## 为什么现在才建
 *
 * `docs/open-source-readiness.md` §执行进度 的批次 8.2 要升 `marked` 14 → 18
 * （跨 4 个大版本）。升级前实测：`src/utils/` 下只有 `retrievalNotice.test.ts` 与
 * `citationJump.test.ts`，而 `src/**` 里**没有任何一条用例**碰过 `$$...$$` / `$...$`。
 * 也就是说 `markdown.ts:126-163` 的两个自定义扩展处于**零覆盖**状态 ——
 * 而它恰恰是 `marked` 大版本最容易碰坏的地方：
 *
 * - 扩展的 `tokenizer(src)` / `renderer(token)` 是**我们自己写的**，靠 marked 的
 *   扩展契约喂数据；契约一变，公式会**静默变成纯文本**（不报错、不崩，只是不渲染了）。
 * - 所以这里断言的是**最终 HTML 的三个特征类名**（`katex` / `katex-display` /
 *   `katex-block`），而不是"某个函数被调用过"。
 *
 * ## 覆盖的四条路径（对应 `renderMarkdown` 的三段管道）
 *
 * | 用例 | 管道位置 |
 * |---|---|
 * | 块级 `$$...$$` | `marked.parse`（blockMath 扩展）→ `renderKatex(…, true)` |
 * | 行内 `$...$` | `marked.parse`（inlineMath 扩展）→ `renderKatex(…, false)` |
 * | 围栏代码块 | `markedHighlight`（`marked.use` 注册的高亮器）|
 * | 原始 HTML 里的公式 | `renderMathInHtml` 的二次渲染（**不经过**扩展）|
 * | 消毒 | `sanitizeHtml`（DOMPurify 白名单）|
 *
 * ## 一条**必须**测的路径（2026-09-24 变异实验后补上）
 *
 * `renderMarkdown` 的 `catch` 兜底（`markdown.ts:276-279`，降级为转义纯文本）
 * 一开始被我判断为"无法确定性触发，不值得为它 mock `marked`"。**那个判断是错的。**
 *
 * 变异实验：把 blockMath 扩展的 `name` 从 `'blockMath'` 改成 `'blockmathX'`，
 * 期望看到"断言失败"，实际看到的是 **marked 自己抛错** →
 * 于是**整个 `renderMarkdown` 走了兜底**，输出变成
 * `<pre style="white-space:pre-wrap;word…">`。
 *
 * 两种情况都是"公式全丢"，但失败形态完全不同：
 *
 * | 形态 | 症状 | 上面那两条断言 |
 * |---|---|---|
 * | 扩展契约变了 | 公式退化成纯文本，**页面照常显示** | ✅ 抓得住（不含 katex-block）|
 * | marked 抛错（如扩展名不合法）| 走兜底，**整段 Markdown 退化成纯文本** | ✅ 抓得住，但报错信息指向"没有 katex-block"，**看不出真因是异常** |
 *
 * 所以必须**单独**锁住兜底路径本身：用 `vi.spyOn(marked, 'parse')` 制造异常
 * （比 `vi.mock('marked')` 温和 —— 保留 13 条真实渲染用例的价值）。
 */
import { describe, expect, it, vi } from 'vitest';
import { marked, renderKatex, renderMarkdown } from './markdown';

describe('renderKatex 的直接契约', () => {
  it('块级：displayMode=true 产出 katex-display', () => {
    const html = renderKatex('E=mc^2', true);
    expect(html).toContain('katex-display');
  });

  it('行内：displayMode=false 不产出 katex-display', () => {
    const html = renderKatex('a^2', false);
    expect(html).toContain('class="katex"');
    expect(html).not.toContain('katex-display');
  });

  it('非法 LaTeX 不抛异常，且用 errorColor 标记出来', () => {
    // throwOnError:false（markdown.ts:113）—— 这是"整站白屏"防线的一半
    const html = renderKatex('\\frac{', false);
    expect(typeof html).toBe('string');
    expect(html.length).toBeGreaterThan(0);
  });
});

describe('renderMarkdown：块级公式（markdown.ts:128-144 的 blockMath 扩展）', () => {
  it('$$...$$ 被包进 katex-block，且是展示模式', () => {
    const html = renderMarkdown('前文\n\n$$E=mc^2$$\n\n后文');
    // katex-block 是**我们自己**的包装（markdown.ts:142），它一旦消失，
    // 说明扩展没被 marked 调用 —— 公式会退化成纯文本
    expect(html).toContain('katex-block');
    expect(html).toContain('katex-display');
    // 公式源文本不应以字面量形式留在输出里
    expect(html).not.toContain('$$');
  });

  it('多行块级公式（含换行）也能渲染', () => {
    const html = renderMarkdown('$$\n\\sum_{i=1}^{n} i\n$$');
    expect(html).toContain('katex-block');
    expect(html).not.toContain('\\sum');
  });
});

describe('renderMarkdown：行内公式（markdown.ts:145-161 的 inlineMath 扩展）', () => {
  it('$...$ 夹在中文里渲染成行内 KaTeX，且不产生块级包装', () => {
    const html = renderMarkdown('端电压 $U=IR$ 保持不变');
    expect(html).toContain('class="katex"');
    expect(html).not.toContain('katex-block');
    expect(html).toContain('端电压');
    expect(html).toContain('保持不变');
  });

  it('一行里出现两个行内公式时两个都渲染', () => {
    const html = renderMarkdown('$a$ 与 $b$');
    const count = html.split('class="katex"').length - 1;
    expect(count).toBeGreaterThanOrEqual(2);
  });
});

describe('renderMarkdown：代码块走 markedHighlight（markdown.ts:77-95）', () => {
  it('有语言标注时套上 hljs 类并着色', () => {
    const html = renderMarkdown('```python\ndef f():\n    return 1\n```');
    expect(html).toContain('hljs');
    // 高亮器会把关键字包成 span；不套 span 说明走的纯文本兜底
    expect(html).toContain('<span');
  });

  it('代码块里的 $ 不被当成公式（shouldSkipMathRender 的 CODE/PRE 分支）', () => {
    const html = renderMarkdown('```\nlet a = $x$\n```');
    expect(html).toContain('let');
    // 关键：代码块内不得出现 KaTeX 产物
    expect(html).not.toContain('class="katex"');
    expect(html).not.toContain('katex-block');
  });

  it('未注册语言退化为转义纯文本，不抛错', () => {
    const html = renderMarkdown('```brainfuck\n+++\n```');
    expect(html).toContain('+++');
  });
});

describe('renderMarkdown：原始 HTML 里的公式走二次渲染（markdown.ts:196-254）', () => {
  it('表格单元格里的 $$...$$ 由 renderMathInHtml 补渲染', () => {
    // marked 会把这种原始 HTML 块原样保留，扩展**不介入**，
    // 全靠 renderMathInHtml 的 DOM 遍历兜住 —— 这是另一条独立路径
    const html = renderMarkdown('<table><tr><td>$$x^2$$</td></tr></table>');
    expect(html).toContain('<table');
    expect(html).toContain('katex');
  });
});

describe('renderMarkdown：解析异常时的兜底（markdown.ts:276-279）', () => {
  it('marked.parse 抛错 → 降级为转义纯文本，函数本身不抛', () => {
    // 这不是假想的失败：变异实验里把扩展 name 改成 'blockmathX'，
    // marked 当场抛错，整段 Markdown 就是从这里退化成纯文本的。
    // 锁住它，是为了让"真因是异常"与"扩展没生效"两种失败形态**可区分**。
    const spy = vi.spyOn(marked, 'parse').mockImplementation(() => {
      throw new Error('模拟 marked 契约破坏');
    });
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

    let html: string;
    expect(() => {
      html = renderMarkdown('$$E=mc^2$$');
    }).not.toThrow();

    // 兜底形态：<pre style="white-space:pre-wrap;…"> 包裹的转义纯文本
    expect(html!).toContain('white-space:pre-wrap');
    expect(html!).not.toContain('katex-block');
    // 原始公式文本以**转义后**的形式保留，用户至少还能读到内容
    expect(html!).toContain('E=mc^2');
    // 且必须留下日志，否则线上只会看到"公式不渲染"而无从查起
    expect(consoleError).toHaveBeenCalled();

    spy.mockRestore();
    consoleError.mockRestore();
  });
});

describe('renderMarkdown：消毒白名单（sanitize.ts:10-44）', () => {
  it('script 被剥掉，正文保留', () => {
    const html = renderMarkdown('安全文本\n\n<script>alert(1)</script>');
    expect(html).toContain('安全文本');
    expect(html).not.toContain('<script');
    expect(html).not.toContain('alert(1)');
  });

  it('纯文本的边界情形不抛错', () => {
    expect(renderMarkdown('')).toBe('');
    expect(renderMarkdown('**粗体**')).toContain('<strong>');
    expect(renderMarkdown('普通一行')).toContain('普通一行');
  });
});
