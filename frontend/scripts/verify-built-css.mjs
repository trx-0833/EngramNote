/**
 * 产物 CSS 校验（overhaul-plan 5.6 迁移证据）
 *
 * ## 为什么必须看 dist 产物，而不是只看源码
 *
 * 源码里"规则还在"不等于"浏览器里还生效"。CSS Modules 会改写两类东西：
 *   1. 类名 → 哈希；
 *   2. **`@keyframes` 动画名 → 也哈希**（这一条最阴）。
 * 本工具实测抓到过一次：`.feedback-correct` 的 `animation: scaleIn ...`
 * 被改写成 `animation:_scaleIn_1aa19_1 ...`，而产物里
 * **没有任何 `@keyframes _scaleIn_1aa19_1`** —— 规则、选择器、声明
 * 在文本上全都在，只有动画没了。规则清单差集看不见这种损失。
 *
 * ## 它检查什么
 *
 * 1. **动画引用悬空**：`animation-name` / `animation` 简写里用到的每个名字，
 *    必须在**同一个产物文件**里有对应的 `@keyframes`（不是一个文件里定义、
 *    另一个文件里引用就算过 —— 代码分割后另一个文件可能还没加载）。
 * 2. **真正打架的重复定义**：同一个（媒体查询上下文 + 选择器 + **属性**）
 *    被赋了两个不同的值。这是"覆盖战争"的产物级指纹，也是唯一值得报的形态：
 *    同一个选择器分几条规则写**不同**属性（`.x{color}` + `.x{padding}`）
 *    是正常 CSS，不算冲突 —— 第一版没区分这两者，报了 69 条噪音。
 * 3. **级联次序**（第一批新增）：同权重、同元素的「全局类 × 模块类」竞争，
 *    得主必须在产物里仍然是**同一个**。见 `CASCADE_PAIRS`。
 * 4. **迁移切片的选择器是否真的进了产物**（按哈希后的关键字确认）。
 * 5. **退休的全局类名是否彻底消失**（含被哈希成 camelCase 的形态）。
 * 6. **产物新鲜度**：产物 mtime 早于源码 mtime 就报警 —— 否则会拿旧产物
 *    得出"已经修好了"的结论（试点轮真的踩到：构建失败、dist 还是上一版）。
 *
 * 用法：node scripts/verify-built-css.mjs [dist目录]
 */
import fs from 'node:fs'
import path from 'node:path'
import { parseRules, splitSelectors, stripComments } from './lib/css-parse.mjs'

/** 去掉注释（保留一个短别名，读起来比到处写 stripComments 顺） */
const clean = stripComments

/**
 * 剥掉 CSS Modules 的哈希后缀：`_authSubmit_1hcab_41` → `_authSubmit`。
 *
 * ⚠️ 类名匹配必须**先剥哈希再判边界**：产物里 `._authSubmit_1hcab_41` 的
 * 类名后面紧跟 `_`（哈希的分隔符），而 `_` 属于 `\w`，
 * 所以 `\._?authSubmit(?![\w-])` 一条都匹配不上 —— 这个 bug 让
 * "级联次序"这一项第一次跑起来时报的是"两侧没有同属性竞争"（假通过）。
 */
const stripHash = (s) => s.replace(/_([0-9a-z]{5})_\d+(?![0-9a-z])/g, '')

const distDir = process.argv[2] || 'dist'
const assetsDir = path.join(distDir, 'assets')

const cssFiles = fs.readdirSync(assetsDir).filter((f) => f.endsWith('.css'))
if (cssFiles.length === 0) {
  console.error(`✗ ${assetsDir} 下没有 CSS：先跑 npm run build`)
  process.exit(2)
}

const sheets = cssFiles.map((f) => ({
  name: f,
  css: fs.readFileSync(path.join(assetsDir, f), 'utf8'),
  mtime: fs.statSync(path.join(assetsDir, f)).mtimeMs,
}))
const all = sheets.map((s) => s.css).join('\n')

let failed = false

// ── 0. 产物新鲜度 ──
const distMtime = Math.max(...sheets.map((s) => s.mtime))
const srcCssFiles = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p)
    else if (e.name.endsWith('.css')) srcCssFiles.push(p)
  }
})(path.join(process.cwd(), 'src'))
const newestSrc = Math.max(...srcCssFiles.map((f) => fs.statSync(f).mtimeMs))
if (newestSrc > distMtime) {
  failed = true
  console.log(
    `✗ 产物比源码旧（源码最新 ${new Date(newestSrc).toISOString()} > 产物 ${new Date(distMtime).toISOString()}）：` +
      `先重新构建，否则下面的结论是拿旧产物得出的`,
  )
} else {
  console.log('✓ 产物比 src 下所有 CSS 都新（不是陈旧产物）')
}

// ── 1. @keyframes 定义 vs 动画引用（**逐文件**比，不跨文件）──
const NOT_A_NAME =
  /^(none|infinite|linear|ease|ease-in|ease-out|ease-in-out|alternate|alternate-reverse|reverse|forwards|backwards|both|normal|running|paused|initial|inherit|unset|revert|steps|linear\(.*\)|cubic-bezier\(.*\))$/

/** 从 animation 简写里挑出动画名：跳过时长/曲线/计数/关键字等非标识符位置 */
function animationNamesFrom(value) {
  const names = []
  for (const group of value.split(',')) {
    for (const tok of group.trim().split(/\s+/)) {
      if (!tok) continue
      if (/^[\d.]/.test(tok)) continue // 时长、延迟（0.3s / 200ms / 0）
      if (NOT_A_NAME.test(tok)) continue
      if (!/^[A-Za-z_-][\w-]*$/.test(tok)) continue
      names.push(tok)
    }
  }
  return names
}

let danglingTotal = 0
const definedKeyframesAll = new Set()
console.log('\n动画引用 vs 定义（逐个产物文件核对）：')
for (const s of sheets) {
  const defined = new Set([...clean(s.css).matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]))
  for (const n of defined) definedKeyframesAll.add(n)
  const used = new Set()
  for (const m of clean(s.css).matchAll(/(?:^|[;{])\s*animation(?:-name)?\s*:\s*([^;{}]+)/g)) {
    for (const name of animationNamesFrom(m[1])) used.add(name)
  }
  const dangling = [...used].filter((n) => !defined.has(n))
  danglingTotal += dangling.length
  if (dangling.length) {
    failed = true
    console.log(`   ✗ ${s.name}: 引用了本文件里没有的 @keyframes → ${dangling.join(', ')}`)
  } else if (used.size) {
    console.log(`   ✓ ${s.name}: 引用 ${used.size} 个动画，全部在本文件内有定义（${[...used].join(', ')}）`)
  } else {
    console.log(`   · ${s.name}: 没有动画引用`)
  }
}
console.log(
  danglingTotal === 0
    ? `✓ 悬空动画引用 0 条（全部产物内 @keyframes 定义 ${definedKeyframesAll.size} 个）`
    : `✗ 悬空动画引用 ${danglingTotal} 条`,
)

// ── 2. 真正打架的重复定义（同上下文 + 同选择器 + 同属性、值不同）──
// 解析统一走 lib/css-parse.mjs：这里原本自己写了一份 parseRules + splitSelectors，
// 而同一个算法在另外两个脚本里各带过一个 bug（媒体查询内的规则被静默丢掉、
// 属性选择器里的逗号被当成选择器分隔符）。同构代码抄三遍就是三次出错机会。

/** 把声明列表转成 属性 -> 值 */
function declsOf(rule) {
  const m = new Map()
  for (const [p, v] of rule.decls) m.set(p.trim().toLowerCase(), v.replace(/\s+/g, ' ').trim())
  return m
}

// key = 上下文 + 选择器 + 属性  → 第一次出现的 {值, 文件}
const propSeen = new Map()
const clashes = []
for (const s of sheets) {
  for (const r of parseRules(s.css)) {
    // at-rule 不是选择器：`@font-face` 被解析器当成一条"规则"，而多个
    // `@font-face` 块的 `src` / `font-family` 天然不同 —— 不排除的话
    // 会稳定产出 49 条"冲突"，把真正的冲突淹掉（试点轮的产物校验里就有这个噪音）。
    if (r.selector.trim().startsWith('@')) continue
    const dm = declsOf(r)
    for (const sel of splitSelectors(r.selector)) {
      if (sel.trim().startsWith('@')) continue
      for (const [prop, val] of dm) {
        const key = `${r.context} ${sel} :: ${prop}`
        if (propSeen.has(key)) {
          const prev = propSeen.get(key)
          if (prev.val !== val) {
            clashes.push({ key, a: prev, b: { val, file: s.name } })
          }
        } else {
          propSeen.set(key, { val, file: s.name })
        }
      }
    }
  }
}

// ── 把打架的选择器归因回**源样式表** ──
// 产物是打包后的 index.css，只报产物文件名回答不了"这是哪两个样式表在打架"，
// 而那正是下一轮要排的雷。所以直接回源文件里找：哪个 `src/**/*.css`
// 定义了这条选择器，就把源文件名附上。
// 第一批修正：模块文件不在 `src/styles/` 下，原来只扫那一个目录，
// 于是模块里的规则**一条都归因不到**（会被算进"未能归因"）。
const srcSheets = srcCssFiles.map((abs) => ({
  name: path.relative(path.join(process.cwd(), 'src'), abs).replace(/\\/g, '/'),
  rules: parseRules(fs.readFileSync(abs, 'utf8')),
}))

/** 某选择器（去掉上下文）出现在哪些源样式表里；同文件出现多次也算多次 */
function sourceFilesFor(selectorText) {
  const bare = selectorText.trim()
  const hits = []
  for (const sh of srcSheets) {
    for (const r of sh.rules) {
      if (splitSelectors(r.selector).some((x) => x.trim() === bare)) hits.push(sh.name)
    }
  }
  return hits
}

// 有意覆盖 vs 需要交代的冲突，靠**同一文件内**判断：
//   - 跨文件 且 后者是补丁层（responsive / refinements / markdown-extras）
//     ⇒ 这是本项目的既有分层方式（基础层 + 补丁层），报出来但不算失败；
//   - 同一文件内同选择器同属性写两遍 ⇒ 后者必胜，前者是死声明，值得单独看。
// 归因：本轮迁移动过的样式表 + 新模块（见 TOUCHED_BY_THIS_MIGRATION），
// 别处冒出来的冲突是迁移前就存在的债，不能算到这次改动头上。
const TOUCHED_BY_THIS_MIGRATION = new Set([
  // 试点（quiz 切片）
  'styles/learning.css',
  'styles/responsive.css',
  'components/quiz/QuizAnswerCard.module.css',
  'components/quiz/SelfRatingButtons.module.css',
  // 第一批：auth → cleaning → diff → dashboard
  'styles/auth.css',
  'styles/cleaning.css',
  'styles/diff.css',
  'styles/dashboard.css',
  'pages/Auth.module.css',
  'components/CleaningPanel.module.css',
  'components/DiffView.module.css',
  'pages/Dashboard.module.css',
])

const annotated = clashes.map((c) => {
  const sel = c.key.split(' :: ')[0].trim()
  // key 形如 `@media (max-width: 768px) .foo`：上下文与选择器之间只有一个空格，
  // 而上下文自身的 `)` 也带空格，所以不能用贪婪的 `^@media.*\)\s*` 去切
  // （那会把选择器一起吃掉，导致既匹配不到源文件、也漏掉"未归因"的统计）。
  // 这里改成"从第一个不在括号内的空格处切"。
  const selOnly = stripMediaContext(sel)
  return { ...c, sources: sourceFilesFor(selOnly), selOnly }
})

/** 去掉 `@media (...) ` 前缀，返回纯选择器 */
function stripMediaContext(s) {
  if (!s.startsWith('@')) return s
  let depth = 0
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (ch === '(') depth++
    else if (ch === ')') {
      depth--
      if (depth === 0) return s.slice(i + 1).trim()
    }
  }
  return s
}

const unattributed = annotated.filter((c) => c.sources.length === 0)
const preExisting = annotated.filter(
  (c) => c.sources.length > 0 && !c.sources.some((s) => TOUCHED_BY_THIS_MIGRATION.has(s)),
)
const migratedSlice = annotated.filter((c) => c.sources.some((s) => TOUCHED_BY_THIS_MIGRATION.has(s)))

console.log(
  `\n同选择器同属性不同值共 ${annotated.length} 条。按**源文件**归因：` +
    `落在本轮改动范围内 ${migratedSlice.length} 条、` +
    `迁移前既有 ${preExisting.length} 条、未能归因 ${unattributed.length} 条`,
)

if (migratedSlice.length) {
  failed = true
  console.log(`\n✗ 落在本轮改动范围内的冲突 ${migratedSlice.length} 条（必须处理）：`)
  for (const c of migratedSlice.slice(0, 20)) {
    console.log(`   - ${c.key}   [${c.sources.join(', ')}]`)
    console.log(`       ${c.a.val}  →  ${c.b.val}`)
  }
}

if (unattributed.length) {
  // 未能归因不等于"没问题"：可能是新写法让回源匹配失效了。
  // 所以列出选择器，让人能一眼看出是不是自己刚改的那批。
  console.log(
    `\n⚠ 未能归因到源文件 ${unattributed.length} 条（多为属性选择器/自定义属性等写法）：`,
  )
  const sels = [...new Set(unattributed.map((c) => c.selOnly))]
  console.log(`   ${sels.slice(0, 12).join(' / ')}${sels.length > 12 ? ` …(+${sels.length - 12})` : ''}`)
}

if (preExisting.length) {
  console.log(`\n⚠ 迁移前既有的冲突 ${preExisting.length} 条（本轮不处理，下一轮排雷用）：`)
  const bySources = new Map()
  for (const c of preExisting) {
    const k = c.sources.join(' + ')
    if (!bySources.has(k)) bySources.set(k, [])
    bySources.get(k).push(c)
  }
  for (const [pair, list] of [...bySources].sort((a, b) => b[1].length - a[1].length)) {
    const sels = [...new Set(list.map((c) => c.key.split(' :: ')[0].trim()))]
    console.log(`   - ${pair}：${list.length} 条属性冲突`)
    console.log(
      `       选择器：${sels.slice(0, 10).join(' / ')}${sels.length > 10 ? ` …(+${sels.length - 10})` : ''}`,
    )
  }
}

// ── 3. 级联次序：同元素上「全局类 × 模块类」的竞争，得主不能变 ──
//
// ## 为什么需要这一项（第一批实测，试点轮没暴露）
//
// Vite 按**模块图顺序**产出 CSS：`main.tsx` 第 4 行就 `import App`，样式表在
// 第 7 行之后才引入，所以**静态引入的组件，其模块 CSS 排在全部全局样式表之前**。
// 实测产物 index.css 的字节位置：`Auth.module.css` = 1、`:root`(base.css) = 2550、
// `.btn`(components.css) = 6554。于是"模块规则覆盖全局规则"这种写法
// **在源码里看着对、在产物里是反的** —— `.auth-submit` 的
// `padding / font-size / font-weight / transition` 会被 `.btn` 盖掉。
// 这类反转在文本差集里完全看不出来（两条规则都还在，值也没改），
// 只有算一遍"谁最终生效"才发现，所以在这里逐属性算。
//
// 修法：模块里把全局类写进选择器（`:global(.btn).authSubmit`），
// 权重 (0,2,0) > (0,1,0)，与文件先后无关。
//
// 声明格式：
//   moduleClass —— 模块里的类名（产物里是 `._<name>_<hash>_<line>`）
//   globalClass —— 全局类名（产物里原样，形如 `.btn`）
//   expect      —— 期望的得主（'module' 表示迁移前后都是模块这条规则赢）
const CASCADE_PAIRS = [
  {
    label: 'Login/Register 提交按钮 className={`btn ${styles.authSubmit}`}',
    moduleClass: 'authSubmit',
    globalClass: '.btn',
    expect: 'module',
  },
]

/**
 * 极简权重计算：只处理本项目用到的形态
 * （元素 / `.类` / `#id` / `:伪类` / `::伪元素` / `[属性]` / `:not(...)` 不展开）。
 * 返回 [id, class, type] 三元组，逐个比大小。
 */
function specificity(sel) {
  const s = sel.replace(/:where\([^)]*\)/g, '') // :where() 权重为 0，本项目未用，留着以防万一
  const ids = (s.match(/#[\w-]+/g) || []).length
  const classes =
    (s.match(/\.[\w-]+/g) || []).length +
    (s.match(/\[[^\]]*\]/g) || []).length +
    (s.match(/:(?!:)[\w-]+/g) || []).length
  const pseudoEls = (s.match(/::[\w-]+/g) || []).length
  const types = (s.match(/(^|[\s>+~(,])([a-zA-Z][\w-]*)/g) || []).length + pseudoEls
  return [ids, classes, types]
}
const cmpSpec = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]

console.log('\n级联次序（同元素上全局类 × 模块类的竞争）：')
for (const pair of CASCADE_PAIRS) {
  const collect = (predicate) => {
    const out = []
    for (const s of sheets) {
      let idx = 0
      for (const r of parseRules(s.css)) {
        if (r.context) continue // 只比顶层，媒体查询里的规则不参与
        for (const sel of splitSelectors(r.selector)) {
          idx++
          // 比对前先剥哈希，两侧才能用同一套写法描述（见 stripHash 的注释）
          const bare = stripHash(sel)
          if (!predicate(bare)) continue
          for (const [prop, val] of r.decls) {
            out.push({
              sel: bare,
              prop: prop.trim().toLowerCase(),
              val: val.replace(/\s+/g, ' ').trim(),
              order: idx,
              file: s.name,
            })
          }
        }
      }
    }
    return out
  }
  // 全局侧：选择器**恰好**是 `.btn`（不带伪类、不带其它类）
  const globalDecls = collect((sel) => sel.trim() === pair.globalClass)
  // 模块侧：选择器里含模块类名，且不含任何 `:`（排除 :hover / ::after 等）
  const moduleDecls = collect(
    (sel) => new RegExp(`\\._?${pair.moduleClass}(?![\\w-])`).test(sel) && !sel.includes(':'),
  )
  const compared = []
  let bad = 0
  for (const g of globalDecls) {
    for (const mm of moduleDecls.filter((x) => x.prop === g.prop)) {
      if (g.val === mm.val) continue // 值相同就不存在"谁赢"的问题
      const sp = cmpSpec(specificity(mm.sel), specificity(g.sel))
      let winner
      if (sp !== 0) winner = sp > 0 ? 'module' : 'global'
      else if (mm.file === g.file) winner = mm.order > g.order ? 'module' : 'global'
      // 同权重 + 不同产物文件 ⇒ 胜负取决于加载顺序，这里判不了，
      // 必须显式报出来（"+ 用选择器强化消除歧义"正是修法）
      else winner = 'unknown'
      compared.push({ prop: g.prop, moduleVal: mm.val, globalVal: g.val, winner, moduleSel: mm.sel })
      if (winner !== pair.expect) bad++
    }
  }
  if (bad) {
    failed = true
    console.log(`   ✗ ${pair.label}：${bad} 个属性的得主**不是**期望的 ${pair.expect}`)
    for (const c of compared.filter((x) => x.winner !== pair.expect)) {
      console.log(
        `      - ${c.prop}: 得主 ${c.winner}（模块 ${c.moduleVal} / 全局 ${c.globalVal}，选择器 ${c.moduleSel}）`,
      )
    }
  } else if (compared.length === 0) {
    console.log(`   ⚠ ${pair.label}：两侧没有同属性竞争，这一项没起到检查作用（是不是选择器改名了？）`)
  } else {
    console.log(
      `   ✓ ${pair.label}：${compared.length} 个同名属性全部由 ${pair.expect} 生效` +
        `（${compared.map((c) => c.prop).join(', ')}）`,
    )
  }
}

// ── 4. 迁移切片是否真的进产物 ──
// 按批次列：类名（哈希后仍保留原名）与动画名。任何一个 0 次命中都说明
// "规则搬了但类名没挂上"或"动画定义没搬进来"。
const SLICE_MARKERS = [
  // 试点：quiz 切片
  'quizOption',
  'quizOptionSelected',
  'feedbackCorrect',
  'feedbackIncorrect',
  'feedbackScaleIn',
  'feedbackShake',
  'selfRatingBtn',
  // 第一批：auth
  'authBg',
  'authCard',
  'authTitle',
  'authInputGroup',
  'authInputIcon',
  'authSubmit',
  'authFooter',
  'authScaleIn',
  // 第一批：cleaning
  'cleaningPanel',
  'cleaningStats',
  'cleaningProgress',
  'cleaningProgressBar',
  'cleaningPulse',
  'duplicateBlocks',
  'duplicateBlock',
  'duplicateBlockHeader',
  'duplicateBlockInfo',
  'duplicateBlockIndex',
  'duplicateBlockSimilarity',
  'duplicateBlockActions',
  'duplicateBlockCompare',
  'duplicateBlockTextLabel',
  'duplicateBlockTextContent',
  'duplicateBlockTextEmpty',
  // 第一批：diff
  'diffContainer',
  'diffSummary',
  'diffHeader',
  'diffColLabel',
  'diffBody',
  'diffBlock',
  'diffLine',
  'diffLinePrefix',
  'diffLineNumber',
  'diffLineContent',
  'diffLineRemoved',
  'diffLineAdded',
  'diffLineUnchanged',
  // 第一批：dashboard（私有部分）
  'dashboardTwoCol',
  'dashboardReviewCard',
  'trendBar',
  'trendBarWarning',
]
console.log('\n迁移切片类名/动画在产物中的出现次数：')
for (const marker of SLICE_MARKERS) {
  const n = (all.match(new RegExp(marker, 'g')) || []).length
  if (n === 0) failed = true
  console.log(`   ${n === 0 ? '✗' : '✓'} ${marker}: ${n}`)
}

// ── 5. 退休的全局类名不得再出现在产物 CSS 里 ──
// 注意：模块化之后同一个类名会以 camelCase + 哈希的形态存在
// （`.quiz-option` → `._quizOption_hash`），所以这里查的是**kebab 形态**。
const RETIRED = [
  // 试点
  'quiz-option',
  'quiz-option-selected',
  'feedback-correct',
  'feedback-incorrect',
  'feedback-pending',
  'self-rating-btn',
  // 第一批
  'auth-bg',
  'auth-card',
  'auth-title',
  'auth-input-group',
  'auth-input-icon',
  'auth-submit',
  'auth-footer',
  'cleaning-panel',
  'cleaning-stats',
  'cleaning-progress',
  'cleaning-progress-bar',
  'duplicate-blocks',
  'duplicate-block',
  'duplicate-block-header',
  'duplicate-block-info',
  'duplicate-block-index',
  'duplicate-block-similarity',
  'duplicate-block-actions',
  'duplicate-block-compare',
  'duplicate-block-text-label',
  'duplicate-block-text-content',
  'duplicate-block-text-empty',
  'diff-container',
  'diff-summary',
  'diff-header',
  'diff-col-label',
  'diff-body',
  'diff-block',
  'diff-line',
  'diff-line-prefix',
  'diff-line-number',
  'diff-line-content',
  'diff-line-removed',
  'diff-line-added',
  'diff-line-unchanged',
  'dashboard-two-col',
  'dashboard-review-card',
  'trend-bar',
  'trend-bar-warning',
]
console.log('\n已退休的全局类名（在产物 CSS 里应当彻底消失）：')
let retiredHits = 0
for (const r of RETIRED) {
  // 用边界匹配，避免把 `.quiz-option-editor` 之类误判
  const hits = [...all.matchAll(new RegExp(`\\.${r.replace(/-/g, '\\-')}(?![\\w-])`, 'g'))].length
  if (hits > 0) {
    retiredHits++
    failed = true
    console.log(`   ✗ .${r}: ${hits}`)
  }
}
console.log(
  retiredHits === 0
    ? `   ✓ ${RETIRED.length} 个退休类名在产物中全部为 0 次`
    : `   ✗ ${retiredHits} 个退休类名仍然出现在产物里`,
)

process.exit(failed ? 1 : 0)
