/**
 * @file 卡面的样式护栏（visual-refactor-plan 批次 E3）
 *
 * ## 为什么用"读样式表"的方式测
 *
 * 批次 E3 对卡面提了三条**样式级**的硬要求，它们全都在 jsdom 的能力边界之外：
 *
 * | 要求 | 为什么 DOM 测不了 |
 * |---|---|
 * | 翻面不超过 200ms | jsdom 不跑 CSS 动画，`getComputedStyle` 也读不到 `animation` 的时长 |
 * | 尊重 `prefers-reduced-motion` | jsdom 30 **没有** `window.matchMedia`，媒体查询块被整块跳过（实测见 `mobile-input-font-size.test.ts` 文件头） |
 * | 颜色只走令牌 | 本项目没开 `test.css`，`.css` 导入在 Vitest 里是空串 |
 *
 * 所以这里从磁盘读 `CardFace.module.css` 与 `base.css`（令牌真值来源），
 * 按源码断言。翻面"翻的是哪一面"由 `CardReview.test.tsx` 从 DOM 侧守。
 *
 * ## 两条防空检查
 *
 * 一个读文件的护栏最容易以"什么都没扫到"的方式静默失效，所以：
 * 第一条，读到空文本就报错；第二条，扫不到任何时长声明也报错 —— 不是"没问题"。
 */
import { describe, expect, it, vi } from 'vitest';

/** 只声明本文件用到的那几个 API（本项目没有 @types/node） */
interface NodeFs {
  readFileSync(path: string, encoding: 'utf8'): string;
}

const fs = await vi.importActual<NodeFs>('node:fs');

/**
 * 本文件所在目录 = `src/pages/cardreview`。
 *
 * 刻意**不在模块作用域求值**：`expect.getState().testPath` 要等用例开始跑才有值，
 * 模块加载期拿到的是 `undefined` —— 那样路径会拼成 `/CardFace.module.css`，
 * 报错信息还会指着一份"看起来没问题"的相对路径。
 */
function dir(): string {
  const testPath = expect.getState().testPath;
  if (!testPath) throw new Error('expect.getState().testPath 为空：定位不到测试文件所在目录');
  return testPath.replace(/[/\\][^/\\]*$/, '');
}

/** 读一个相对本目录的文件（读到空内容 / 读不到都直接报错，绝不静默放过） */
function read(relPath: string): string {
  const text = fs.readFileSync(`${dir()}/${relPath}`, 'utf8');
  expect(text.length, `读不到 ${relPath}：护栏失明，不是"没问题"`).toBeGreaterThan(0);
  return text;
}

/** 是否在本仓库的注释里（这些文件按惯例带大量中文说明，注释里会出现色值与写法示例） */
const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

let cssCache: string | null = null;

function cardFaceCss(): string {
  if (cssCache === null) cssCache = stripComments(read('./CardFace.module.css'));
  return cssCache;
}

/** 取出某个 at-rule 的**块体**（含嵌套大括号的配对处理） */
function extractBlocks(text: string, head: RegExp): string[] {
  const out: string[] = [];
  const re = new RegExp(head.source, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const open = text.indexOf('{', m.index + m[0].length - 1);
    if (open === -1) continue;
    let depth = 0;
    for (let i = open; i < text.length; i += 1) {
      if (text[i] === '{') depth += 1;
      else if (text[i] === '}') {
        depth -= 1;
        if (depth === 0) {
          out.push(text.slice(open + 1, i));
          break;
        }
      }
    }
  }
  return out;
}

/** 单条规则的声明块（选择器精确匹配，不匹配 `.a, .b` 这种组里的成员） */
function ruleBody(selector: string): string | null {
  const re = new RegExp(`${selector.replace(/\./g, '\\.')}\\s*\\{([^}]*)\\}`);
  const m = re.exec(cardFaceCss());
  return m ? m[1] : null;
}

/** `base.css` 里 `--duration-*` 的真值（时长令牌的唯一来源） */
function durationTokens(): Map<string, number> {
  const base = stripComments(read('../../styles/base.css'));
  const out = new Map<string, number>();
  for (const m of base.matchAll(/(--duration-[a-z]+)\s*:\s*([\d.]+)ms\s*;/g)) {
    out.set(m[1], Number.parseFloat(m[2]));
  }
  expect(out.size, 'base.css 里一个 --duration-* 都没解析到：护栏失明').toBeGreaterThan(0);
  return out;
}

describe('卡面（CardFace.module.css）：批次 E3 的三条硬要求', () => {
  it('★ 反面是金色淡底、正面是白底 + 1px 边框（Heptabase 卡片正面那一条）', () => {
    const front = ruleBody('.cardFace');
    expect(front, '取不到 .cardFace：护栏会失明').not.toBeNull();
    expect(front!).toMatch(/background\s*:\s*var\(--color-surface\)/);
    expect(front!).toMatch(/border\s*:\s*1px solid/);

    const back = ruleBody('.cardFaceBack');
    expect(back, '取不到 .cardFaceBack：护栏会失明').not.toBeNull();
    expect(back!, '背面必须转金色淡底').toMatch(/background\s*:\s*var\(--color-accent-light\)/);
  });

  it('卡面自己不叠加第二档阴影（"仅 1 档阴影"由宿主 .card 的 --shadow-sm 承担）', () => {
    // 宿主 `CardReview.tsx` 的 `div.card` 已经有 `--shadow-sm`；
    // 卡面再写一条 box-shadow 就成了两层阴影投在同一个盒子上。
    expect(cardFaceCss(), '卡面里出现 box-shadow：阴影档数会变成 2').not.toMatch(/box-shadow/);
  });

  it('★ 翻面动效不超过 200ms，时长走 --duration-* 令牌', () => {
    const tokens = durationTokens();
    const decls = [...cardFaceCss().matchAll(/\b(animation|transition)\s*:\s*([^;}]+)/g)];
    expect(decls.length, '一条 animation / transition 都没扫到：护栏失明').toBeGreaterThan(0);

    const durations: { where: string; ms: number }[] = [];
    for (const [, prop, value] of decls) {
      for (const token of value.matchAll(/var\((--duration-[a-z]+)\)/g)) {
        const ms = tokens.get(token[1]);
        expect(ms, `${prop} 用了未定义的时长令牌 ${token[1]}`).toBeDefined();
        durations.push({ where: `${prop}: ${value.trim()}`, ms: ms! });
      }
      // 字面量时长（`0.3s` / `300ms`）也一起量：令牌化不是免责条款
      for (const lit of value.matchAll(/([\d.]+)(ms|s)\b/g)) {
        durations.push({
          where: `${prop}: ${value.trim()}`,
          ms: lit[2] === 's' ? Number.parseFloat(lit[1]) * 1000 : Number.parseFloat(lit[1]),
        });
      }
    }
    expect(durations.length, '时长一条都没量到：护栏失明').toBeGreaterThan(0);
    const tooLong = durations.filter((d) => d.ms > 200);
    expect(
      tooLong.map((d) => `${d.where} = ${d.ms}ms`),
      '批次 E3 要求翻面动效不超过 200ms',
    ).toEqual([]);
  });

  it('★ prefers-reduced-motion: reduce 下动画整条关掉', () => {
    const blocks = extractBlocks(cardFaceCss(), /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(blocks.length, '一个 reduced-motion 媒体块都没有：这条要求没落地').toBeGreaterThan(0);
    expect(
      blocks.some((b) => /animation\s*:\s*none/.test(b)),
      'reduced-motion 块里必须把 animation 关掉（Dialog.module.css:151 / NotesList.module.css:192 的写法）',
    ).toBe(true);
  });

  it('★ 媒体块排在基础规则**之后**（否则那条 reduced-motion 会被同权重的顶层规则压掉）', () => {
    // 媒体查询不增加权重：同选择器同属性时胜负只看源序。
    // 这条与 `verify-built-css.mjs` 的"跨媒体查询覆盖战"是同一件事的单测版。
    const css = cardFaceCss();
    const base = css.indexOf('.cardFace {');
    const media = css.indexOf('@media (prefers-reduced-motion');
    expect(base, '找不到基础规则 .cardFace').toBeGreaterThanOrEqual(0);
    expect(media, '找不到 reduced-motion 媒体块').toBeGreaterThan(base);
  });

  it('★ 动画体定义在本文件内（雷区 1：跨文件引用全局动画名会静默失效）', () => {
    const css = cardFaceCss();
    const used = [...css.matchAll(/animation(?:-name)?\s*:\s*([^;}]+)/g)]
      .flatMap((m) => m[1].split(/[\s,]+/))
      .filter((t) => /^[A-Za-z_][\w-]*$/.test(t) && t !== 'none');
    const definedHere = [...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    expect(used.length, '扫不到任何被引用的动画名：护栏失明').toBeGreaterThan(0);
    expect(definedHere.length, '本文件里没有 @keyframes：动画体没搬进来').toBeGreaterThan(0);
    expect(
      used.filter((name) => !definedHere.includes(name)),
      '这些动画名在本文件里没有定义 —— 名字会被哈希成产物里不存在的 keyframes（静默不播）',
    ).toEqual([]);
  });

  it('★ 模块里没有任何硬编码色值（颜色只走令牌）', () => {
    const hits = [...cardFaceCss().matchAll(/#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\s*\(/g)].map(
      (m) => m[0],
    );
    expect(hits, '新写的颜色必须走 base.css 的令牌（design-drift 会数这个）').toEqual([]);
  });
});
