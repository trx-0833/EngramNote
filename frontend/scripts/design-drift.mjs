/**
 * @file 设计冗余门禁（visual-refactor-plan 批次 F2）
 *
 * ## 为什么需要它
 *
 * 这个仓库对**回归**是零容忍的：`verify-built-css.mjs` 会逐产物文件核对悬空动画、
 * 级联得主、退休类名，`a11y.spec.ts` 的违规登记表是空的（空表 = 零容忍）。
 * 但对**设计冗余完全没有度量** —— 这正是这些东西能长期活下来的原因：
 *
 * | 开工前的实测 | 数量 |
 * |---|---|
 * | `base.css` 里定义了却零引用的令牌 | **11 个**（含"水墨丹青"图谱的三个核心令牌） |
 * | 绕过令牌层的硬编码色值 | **272 处 / 146 个不同值** |
 * | 非令牌的 `font-size` 字面量 | **440 处 / 34 个不同值** |
 * | `9999px` 手写（而 `--radius-full` 只被引用 1 次） | **22 处** |
 *
 * 这些东西没有一条会让测试变红，所以它们只会缓慢增长。本脚本给它们装上分母。
 *
 * ## 口径（三条，缺一不可）
 *
 * 1. **剥注释后再统计**。本仓库的惯例是"删除处留墓碑注释"（写明删了什么、凭什么），
 *    那些注释里**必然出现**被删的令牌名与色值。不剥注释的话，这些检查会对自己的
 *    文档报红 —— 而"给删除留注释"恰恰是本项目要鼓励的做法。
 *    （A4 批次实测踩过：`#9a9ab0` 在全仓的 10 处命中里有 10 处都在注释里。）
 * 2. **基线 + 增量**，不是零容忍。存量还没收敛完就先报红，只会让人把这一段删掉。
 * 3. **分母为 0 就报错**。解析出 0 个文件 / 0 个令牌 / 0 处色值，说明是**检查失明**，
 *    不是"没问题" —— `verify-built-css.mjs` 的每一项检查都带这条自检，这里照做。
 *
 * ## 用法
 *
 * ```bash
 * node scripts/design-drift.mjs              # 门禁：与基线比较，超了就红
 * node scripts/design-drift.mjs --snapshot   # 只打印当前统计（填基线时用）
 * ```
 *
 * 基线的更新必须**显式改本文件的 `BASELINE` 常量**并写明理由 ——
 * 让"放宽门禁"这个动作留下痕迹，而不是脚本自己悄悄把基线推上去。
 */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const SRC = path.join(ROOT, 'src');
const TOKEN_FILE = path.join(SRC, 'styles', 'base.css');

/**
 * 统计基线（`--snapshot` 的输出）。
 *
 * `null` = **尚未设置**。这时门禁会拒绝通过并提示先跑 `--snapshot` ——
 * 用一个假的 0 会让首次运行"全红"，用 `Infinity` 会让它永远绿，两者都在骗人。
 */
const BASELINE = {
  /** 硬编码色值处数（hex / rgb / rgba / hsl / hsla） */
  hardcodedColors: null,
  /** 非令牌的 font-size 处数（CSS 的 font-size 与 TSX 的 fontSize） */
  rawFontSizes: null,
  /** 记录基线的日期与当时的 HEAD，便于日后追溯"为什么是这个数" */
  snapshotDate: '',
};

const SCAN_EXTS = new Set(['.css', '.tsx', '.ts']);

/**
 * 登记在案的"零引用令牌"—— 每条必须写明**理由**与**负责批次**。
 *
 * 为什么需要这个出口：这一轮有两种零引用是**已知且合理**的 ——
 * ① 规范预留（新代码该用它，但存量按既定决策不替换）；
 * ② 该接线但那一批还没做到（有明确的负责批次）。
 * 把它们显式登记，比放宽整条检查更诚实：后来的人能直接看到"谁欠着这笔账"。
 *
 * **自检**：登记项如果在当前代码里已经**不再是**零引用，脚本报"登记过期"。
 * 这一条防的是"账还完了但登记没删"—— 没有它，这张表迟早变成一份没人看的豁免名单。
 */
const PENDING_WIRING = {
  // ── 图谱水墨令牌：canvas 绘制拿不到 CSS 变量 ──
  '--graph-ink': '批次 A4 判定 canvas 用不了 var()，字面量保留在 graph/types.ts；接线待 E4',
  '--graph-ink-faint': '同上，待 E4',
  '--graph-paper-deep': '同上，待 E4',
  // ── 有明确负责批次的（做完那一批就该把这些行删掉，过期自检会提醒）──
  '--shadow-focus': '批次 D4（:focus-visible 统一光环）接线',
  '--width-reading': '批次 C2（页宽三档）接线',
  '--width-standard': '同上，待 C2',
  '--width-full': '同上，待 C2',
  '--z-base': '批次 D2（迁移 5 套 modal）接线；--z-modal 已由 Dialog.module.css 使用',
  '--z-dropdown': '同上，待 D2',
  '--z-drawer': '同上，待 D2',
  '--z-toast': '同上，待 D2',
  // ── 规范预留（**不是欠账**，是刻意留给新代码的命名）──
  '--space-2xs': '规范预留：新写的样式用它；存量 36 处 2px 手写不替换（替换会动外观，留待单独一轮）',
  '--text-2xs': '规范预留：存量按「保持字号不变」的决策不替换，新写的样式必须用它（见 base.css 字号段的长注释）',
  '--text-xs': '同上',
  '--text-sm': '同上',
  '--text-sm-alt': '同上（且它是「存量专用」，本来就不该有新引用）',
  '--text-base': '同上',
  '--text-base-alt': '同上（且它是「存量专用」，本来就不该有新引用）',
  '--text-md': '同上',
  '--text-2xl': '同上',
};

/** 递归收集源文件（跳过测试、生成物与 node_modules） */
function collectFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'generated') continue;
      out.push(...collectFiles(full));
      continue;
    }
    if (!SCAN_EXTS.has(path.extname(entry.name))) continue;
    // 测试文件不计：它们断言的是行为，不是外观；而且用例里会写色值做对照
    if (/\.test\.(ts|tsx)$/.test(entry.name)) continue;
    out.push(full);
  }
  return out;
}

/**
 * 剥掉注释。
 *
 * ⚠️ 行注释的正则要避开 `://` —— 否则 `https://…` 后面的整行都会被当成注释吃掉
 * （本仓库的注释里外链很多，arXiv / GitHub 链接满地都是）。
 */
function stripComments(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/** 从 `base.css` 的 `:root` 块里取出全部令牌名 */
function extractTokens(cssText) {
  const rootMatch = cssText.match(/:root\s*\{([\s\S]*?)\n\}/);
  const scope = rootMatch ? rootMatch[1] : cssText;
  return [...scope.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]);
}

/** 统计某个令牌被 `var()` 引用了多少次 */
function countTokenRefs(files, token) {
  const needle = `var(${token})`;
  let count = 0;
  for (const { text } of files) {
    let index = text.indexOf(needle);
    while (index !== -1) {
      count += 1;
      index = text.indexOf(needle, index + needle.length);
    }
  }
  return count;
}

/**
 * 统计硬编码色值。
 *
 * 口径：`#` 后跟 3/4/6/8 位十六进制，或 `rgb(` / `rgba(` / `hsl(` / `hsla(`。
 * 它会把 CSS 的 ID 选择器（`#foo`）也算进来 —— 本仓库几乎没有（实测个位数），
 * 而**它在"增长"时才报警**，多算的那几个只要不再增加就不会误报。
 */
function countHardcodedColors(files) {
  const hits = [];
  const hex = /#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{4}\b|#[0-9a-fA-F]{3}\b/g;
  const fn = /\b(?:rgba?|hsla?)\s*\(/g;
  for (const { rel, text } of files) {
    for (const m of text.matchAll(hex)) hits.push(`${rel}:${m[0]}`);
    // 用 Array.from 的映射参数，而不是 `for (const _ of …)`：
    // 后者虽然能跑，但会留下一个没被使用的循环变量，被
    // `@typescript-eslint/no-unused-vars` 判红 —— F2 的首次提交正是这么栽的
    // （提交时只验证了"脚本能跑通"，漏跑了 lint）。
    hits.push(...Array.from(text.matchAll(fn), () => `${rel}:rgb()`));
  }
  return hits;
}

/** 统计非令牌的 font-size（CSS 的 `font-size:` 与 TSX 的 `fontSize:`） */
function countRawFontSizes(files) {
  const hits = [];
  const css = /font-size\s*:\s*([^;{}]+)[;}]/g;
  const tsx = /fontSize\s*:\s*([^,}]+)/g;
  for (const { rel, text } of files) {
    for (const re of [css, tsx]) {
      for (const m of text.matchAll(re)) {
        const value = m[1].trim();
        if (value.startsWith('var(--')) continue; // 走令牌的不算
        hits.push(`${rel}: ${value}`);
      }
    }
  }
  return hits;
}

function main() {
  const snapshotOnly = process.argv.includes('--snapshot');
  const files = collectFiles(SRC).map((full) => ({
    rel: path.relative(ROOT, full).replace(/\\/g, '/'),
    // 令牌定义处本身不算"使用"，但色值统计要包含 base.css（它也有非令牌色值）
    text: stripComments(fs.readFileSync(full, 'utf8')),
  }));

  if (files.length === 0) {
    console.error('✗ 一个源文件都没扫到 —— 检查失明，不是"没问题"');
    process.exit(1);
  }

  // ⚠️ 必须用**剥过注释**的文本提取令牌定义。本仓库的惯例是在删除处留墓碑注释
  // （"`--radius-xl: 24px` 于 A2 删除…"），不剥的话那些注释会被当成"令牌还在定义"，
  // 于是**已经删掉的令牌会被报成"零引用"** —— 本脚本第一版正是这么错的：
  // 它报了 24 个零引用，其中 8 个其实早在批次 A2 就删掉了。
  // 这与 `verify-built-css.mjs` 里"比较前必须剥注释"是同一条教训。
  const baseCss = files.find((file) => file.rel === 'src/styles/base.css');
  if (!baseCss) {
    console.error('✗ 没扫到 src/styles/base.css —— 令牌层找不到，检查失明');
    process.exit(1);
  }
  const tokens = extractTokens(baseCss.text);
  if (tokens.length === 0) {
    console.error(`✗ 从 ${path.relative(ROOT, TOKEN_FILE)} 里解析出 0 个令牌 —— 解析器失明`);
    process.exit(1);
  }

  const zeroRefTokens = tokens.filter((token) => countTokenRefs(files, token) === 0);
  // 登记在案的"尚未接线 / 规范预留"不算失败；但登记项必须**真的命中**（见下方自检）
  const unusedTokens = zeroRefTokens.filter((token) => !(token in PENDING_WIRING));
  const staleRegistrations = Object.keys(PENDING_WIRING).filter(
    (token) => !zeroRefTokens.includes(token),
  );
  const colors = countHardcodedColors(files);
  const fontSizes = countRawFontSizes(files);

  console.log('设计冗余门禁（visual-refactor-plan F2）');
  console.log(`  扫到 ${files.length} 个源文件、${tokens.length} 个令牌\n`);

  console.log(`① 令牌引用：${tokens.length - unusedTokens.length}/${tokens.length} 个有用引用者`);
  if (unusedTokens.length > 0) {
    console.log(`   ✗ ${unusedTokens.length} 个零引用：${unusedTokens.join('、')}`);
  }

  console.log(`\n② 硬编码色值：${colors.length} 处`);
  console.log(`③ 非令牌 font-size：${fontSizes.length} 处`);

  if (snapshotOnly) {
    console.log('\n--- 快照模式：把下面两个数字填进 BASELINE ---');
    console.log(`  hardcodedColors: ${colors.length},`);
    console.log(`  rawFontSizes: ${fontSizes.length},`);
    console.log(`  snapshotDate: '${new Date().toISOString().slice(0, 10)}',`);
    return;
  }

  let failed = false;

  // 基线未设置：拒绝通过。假基线（0 或 Infinity）都会骗人
  if (BASELINE.hardcodedColors === null || BASELINE.rawFontSizes === null) {
    console.error('\n✗ 基线未设置 —— 先跑 `node scripts/design-drift.mjs --snapshot` 并把结果填进 BASELINE');
    failed = true;
  }

  if (unusedTokens.length > 0) {
    console.error(
      `\n✗ 有 ${unusedTokens.length} 个令牌没有任何引用者 —— 它们要么该接通、要么该删掉；` +
        `\n  "定义了却没人用"正是本脚本要防的东西（开工前有 11 个）:` +
        `\n  ${unusedTokens.join('、')}`,
    );
    failed = true;
  }

  // 登记过期自检：账还完了就得把登记删掉，否则这张表会烂成豁免名单
  if (staleRegistrations.length > 0) {
    console.error(
      `\n✗ PENDING_WIRING 里有 ${staleRegistrations.length} 条**登记过期**（这些令牌现在已经有引用者了）:` +
        `\n  ${staleRegistrations.join('、')}` +
        `\n  ⇒ 请把它们从登记表里删掉，让这张表始终等于"当前真实的欠账"`,
    );
    failed = true;
  }

  if (BASELINE.hardcodedColors !== null) {
    const over = colors.length - BASELINE.hardcodedColors;
    if (over > 0) {
      console.error(
        `\n✗ 硬编码色值比基线多了 ${over} 处（${BASELINE.hardcodedColors} → ${colors.length}）——` +
          `\n  新写的颜色请走令牌（base.css 的 --color-*）；确需字面量时，登记进本文件的 BASELINE 并写明理由`,
      );
      failed = true;
    } else {
      console.log(`   ✓ 未超过基线（${BASELINE.hardcodedColors} → ${colors.length}）`);
    }
  }

  if (BASELINE.rawFontSizes !== null) {
    const over = fontSizes.length - BASELINE.rawFontSizes;
    if (over > 0) {
      console.error(
        `\n✗ 非令牌 font-size 比基线多了 ${over} 处（${BASELINE.rawFontSizes} → ${fontSizes.length}）——` +
          `\n  新写的字号请走 --text-* 令牌`,
      );
      failed = true;
    } else {
      console.log(`   ✓ 未超过基线（${BASELINE.rawFontSizes} → ${fontSizes.length}）`);
    }
  }

  if (failed) process.exit(1);
  console.log('\n✓ 设计冗余门禁通过（基线日期：' + (BASELINE.snapshotDate || '未记录') + '）');
}

main();
