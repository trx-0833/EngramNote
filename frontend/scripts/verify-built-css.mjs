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
import { parseRules, splitSelectors, stripComments, findRecentRev, readFromGit } from './lib/css-parse.mjs'
import {
  collectCascadeDecls,
  findMediaWars,
  findShorthandClashes,
  findCrossClassShorthandClashes,
  coAppliedClassGroups,
  fileRank,
  isSingleClassSelector,
} from './lib/css-cascade.mjs'

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

/**
 * 级联求解用的扁平表（收尾轮新增的两项检查：跨媒体查询覆盖战 / 简写 vs 长写）。
 *
 * ⚠️ **只有"静态层在前、懒加载 chunk 在后"这一条是确定的**：
 * `index-<hash>.css` 由 `<head>` 里的 `<link>` 引入，懒加载页面/组件的 chunk
 * 由 `__vitePreload` 随后注入（计划 §5 雷区 8 的补充说明）。
 * **两个不同 chunk 之间的先后静态判不了**（取决于路由先加载谁），
 * 所以 `lib/css-cascade.mjs` 的 `pickWinner` 在那一种组合上返回 `unknown`
 * —— 那种竞争必须显式报出来，不能猜一个顺序然后当成结论。
 */
const sheetsInCascadeOrder = [...sheets].sort(
  (a, b) => fileRank(a.name) - fileRank(b.name) || a.name.localeCompare(b.name),
)
const cascadeDecls = collectCascadeDecls(sheetsInCascadeOrder)

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
  // 第二批：markdown-extras（ask-ai + selection-menu）
  'styles/markdown-extras.css',
  'components/NoteAskPanel.module.css',
  'pages/notedetail/SelectionMenu.module.css',
  // 第三批：learning 按归属拆分（QA / Upload / NotesList）+ dashboard 余下（StatCard）
  'pages/QA.module.css',
  'pages/Upload.module.css',
  'pages/NotesList.module.css',
  'components/StatCard.module.css',
  // 第四批（序 5）：assessment.css 按归属拆分 + 与 refinements.css 的冲突裁决。
  // 三个全局样式表都真的动过：assessment.css / refinements.css 各删掉自己那一套里
  // **输的**声明，responsive.css 的 480px `.knowledge-points-grid` 搬进模块。
  // 加进这个集合 = "本文件剩下的冲突都必须在本轮解决"，
  // 所以加了它们之后，那 16 条迁移前既有的冲突必须彻底归零（实测确实归零）。
  'styles/assessment.css',
  'styles/refinements.css',
  'pages/LearningAssessment.module.css',
  // `components.css` 只删了两条被 `.card-hover:hover` 压掉的声明
  // （`transform: translateY(-1px)` / `box-shadow: var(--shadow-md)`），
  // 但它确实属于"本批改动范围"，登记进来才能让"该文件再冒出冲突"变成红灯。
  'styles/components.css',
  // 第五批（序 8）：`components.css` 的 5 条页面级布局挂点 +
  // `responsive.css` 里同一批类名的 8 条窄屏规则 → 三个组件模块。
  // 两个全局样式表本来就在上面（components.css 第四批登记过、
  // responsive.css 试点轮登记过），这里补上三个新模块。
  'pages/notedetail/NoteDetailHeader.module.css',
  'pages/notedetail/EditSplitView.module.css',
  // `pages/NotesList.module.css` 第三批已登记（同一份模块里又多了 5 条）
  // 第六批（序 9）：`layout.css` 整节按归属拆分（Sidebar / App 骨架）+
  // `responsive.css` 里命中同一批类名的 12 条窄屏规则。
  'styles/layout.css',
  'components/Sidebar.module.css',
  'App.module.css',
  // 第七批（序 10）：`graph.css` 整份进图谱功能模块 +
  // `responsive.css` 里命中同一批类名的 16 条窄屏规则。
  'styles/graph.css',
  'components/graph/Graph.module.css',
  // 序 6 收尾：`markdown.css` 里最后 5 条 `.adhd-*` 规则进 hook 模块
  // （第三批那个"有记录的停"的收尾）。`markdown.css` 只**删**了这一组，
  // `.markdown-body` 与它的正文规则按判据继续留全局 ——
  // 登记进来 = "本文件里剩下的冲突都必须在本轮解决"。
  'styles/markdown.css',
  'hooks/useAdhdReader.module.css',
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
// Vite 按**模块图顺序**产出 CSS。第一批当时 `main.tsx` 第 4 行就 `import App`、
// 样式表在第 7 行之后才引入，于是**静态引入的组件，其模块 CSS 排在全部全局
// 样式表之前**（实测产物 index.css 字节位置：`Auth.module.css` = 1、
// `:root`(base.css) = 2551、`.btn`(components.css) = 6555）。
// 结果 `.auth-submit` 的 `padding / font-size / font-weight / transition`
// 被全局 `.btn` 反盖 —— 而这类反转在文本差集里**完全看不出来**
// （两条规则都还在，值也没改），只有算一遍"谁最终生效"才发现。
//
// 已落地两层防护：
//   1. **根治**：`main.tsx` 里全局样式表放到组件之前 ⇒ 产物顺序
//      ①令牌 → ②全局 → ③模块，模块规则按源序正常取胜；
//   2. **护栏**：就是下面这段。它不关心你用哪种写法 ——
//      先比权重（`:global(.btn).authSubmit` 那种提权写法靠这一条赢），
//      权重相同时同文件比先后（现在的单类写法靠这一条赢），
//      同权重又跨文件则直接报"判不了"（那时胜负取决于 chunk 加载顺序，
//      必须用提权写法消除歧义）。
//
// 声明格式：
//   moduleClass —— 模块里的类名（产物里是 `._<name>_<hash>_<line>`）
//   globalClass —— 全局类名（产物里原样，形如 `.btn`）
//   expect      —— 期望的得主（'module' 表示模块这条规则必须赢）
//   why         —— 为什么这条竞争值得钉住（写给下一个人看）
const CASCADE_PAIRS = [
  {
    label: 'Login/Register 提交按钮 className={`btn ${styles.authSubmit}`}',
    moduleClass: 'authSubmit',
    globalClass: '.btn',
    expect: 'module',
    why:
      '两处都有 padding / font-size / font-weight / transition，' +
      '谁赢决定按钮是高是矮。踩过一次反转（模块排在全局之前），' +
      '改 main.tsx 顺序才修好 —— 这条一旦红，先看 main.tsx 的导入顺序',
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
    if (pair.why) console.log(`      为什么钉这条：${pair.why}`)
  } else if (compared.length === 0) {
    failed = true
    console.log(
      `   ✗ ${pair.label}：两侧没有同属性竞争 —— 这项检查**没起到作用**。` +
        `多半是选择器改名了或那条规则被删了；确认后请更新/删除 CASCADE_PAIRS 里这一项，` +
        `不要留一条永远绿灯的空检查。`,
    )
  } else {
    const bySpec = compared.some((c) => specificity(c.moduleSel)[1] !== specificity(pair.globalClass)[1])
    console.log(
      `   ✓ ${pair.label}：${compared.length} 个同名属性全部由 ${pair.expect} 生效` +
        `（${compared.map((c) => c.prop).join(', ')}；` +
        `取胜依据=${bySpec ? '权重更高' : '权重相同、同文件靠源序'}）`,
    )
  }
}

// ── 3b. 同文件内、同权重的**模块规则之间**的先后（顺序就是行为）──
//
// ## 为什么 `CASCADE_PAIRS` 覆盖不到这一种（第六批序 9 新增）
//
// `CASCADE_PAIRS` 比的是"全局类 × 模块类"，而这一批最危险的一处竞争**两边都是
// 模块类**：`Sidebar.module.css` 的 768px 块里
//
//   .sidebar, .sidebar-collapsed { transform: translateX(-100%) }   ← 抽屉藏起来
//   .sidebar-mobile-open         { transform: translateX(0) }       ← 抽屉滑出来
//
// 两条权重同为 (0,1,0)、都带同一个类名（同一个元素上并列），媒体查询又不增加权重
// ⇒ **得主只由这一块里的先后决定**。一旦有人"顺手"把 `.sidebar-mobile-open`
// 排到前面，抽屉再也滑不出来，而规则清单差集、冲突统计、动画检查**全都不会响**
// （两条规则都还在、值也没改 —— 与第一批的"级联反转"是同一类事故）。
// 所以这里单独钉住"谁必须排在后面"。
//
// 声明格式：
//   context —— 媒体查询上下文（原样，与解析器给出的字符串一致）
//   prop    —— 竞争的属性
//   winner  —— 必须生效的模块类名（kebab 或 camel 都行，匹配时会剥哈希）
//   losers  —— 与它同权重、必须排在它**前面**的类名
const ORDER_PAIRS = [
  {
    label:
      '移动端抽屉：`.sidebar-mobile-open { transform: translateX(0) }` 必须排在 ' +
      '`.sidebar, .sidebar-collapsed { transform: translateX(-100%) }` 之后',
    context: '@media (max-width: 768px)',
    prop: 'transform',
    winner: 'sidebarMobileOpen',
    losers: ['sidebar', 'sidebarCollapsed'],
    why:
      '三个类名并列在同一个 <nav> 上、权重同为 (0,1,0)，得主只看块内先后。' +
      '顺序反了 = 抽屉再也滑不出来（translateX(0) 被 translateX(-100%) 盖住），' +
      '而这在源码、规则清单、冲突统计里都看不出来。',
  },
  {
    label:
      '图谱按钮的 active+hover 态：`.graph-btn-active:hover` 必须排在 `.graph-btn:hover` 之后' +
      '（第七批序 10 搬进 `components/graph/Graph.module.css` 时顺序一字未改）',
    context: '',
    prop: 'color',
    winner: 'graphBtnActive',
    losers: ['graphBtn'],
    why:
      '`.graph-btn:hover` 与 `.graph-btn-active:hover` 权重同为 (0,2,0)，' +
      '两条都命中"创建关系"按钮的按下态时得主只看先后 —— 顺序反了，' +
      '激活态会退回普通 hover 的墨蓝字/淡底，看起来像按钮没被激活。',
  },
]

console.log('\n同文件内同权重的模块规则先后（顺序就是行为）：')
for (const pair of ORDER_PAIRS) {
  const decls = []
  for (const s of sheets) {
    let idx = 0
    for (const r of parseRules(s.css)) {
      if (r.context !== pair.context) continue
      for (const sel of splitSelectors(r.selector)) {
        idx++
        const bare = stripHash(sel)
        const names = [pair.winner, ...pair.losers]
        const hit = names.find((n) => new RegExp(`\\._?${n}(?![\\w-])`).test(bare))
        if (!hit) continue
        for (const [prop, val] of r.decls) {
          if (prop.trim().toLowerCase() !== pair.prop) continue
          decls.push({ cls: hit, sel: bare, val: val.replace(/\s+/g, ' ').trim(), order: idx, file: s.name })
        }
      }
    }
  }
  const present = new Set(decls.map((d) => d.cls))
  const missing = [pair.winner, ...pair.losers].filter((n) => !present.has(n))
  if (missing.length) {
    failed = true
    console.log(`   ✗ ${pair.label}：产物里找不到 ${missing.join(', ')} 的 ${pair.prop} 声明 —— 这项检查没起作用`)
    continue
  }
  // 得主 = 权重最高者；权重相同则同文件内**更靠后**者
  const winnerOf = decls.reduce((best, d) => {
    const sp = cmpSpec(specificity(d.sel), specificity(best.sel))
    if (sp > 0) return d
    if (sp < 0) return best
    if (d.file === best.file) return d.order > best.order ? d : best
    return best
  })
  if (winnerOf.cls !== pair.winner) {
    failed = true
    console.log(`   ✗ ${pair.label}`)
    console.log(`      实际得主是 ${winnerOf.sel}（${winnerOf.file} @${winnerOf.order}，值 ${winnerOf.val}）`)
    if (pair.why) console.log(`      为什么钉这条：${pair.why}`)
  } else {
    console.log(
      `   ✓ ${pair.label}：得主 = ${winnerOf.sel}（${winnerOf.file}，值 ${winnerOf.val}，` +
        `压过 ${decls.filter((d) => d !== winnerOf).map((d) => d.sel).join(', ') || '（无同属性对手）'}）`,
    )
  }
}

// ── 3c. 跨媒体查询覆盖战（overhaul-plan 5.6 收尾轮新增的检查之一）──
//
// ## 为什么既有三道检查都看不见它
//
// `css-migration-diff.mjs` 按 `(上下文 + 选择器 + 属性)` 建键，于是
// `@media (max-width: 768px) .markdown-body .katex :: font-size` 与
// `.markdown-body .katex :: font-size` 落在**两个键**上，谁赢看不见。
// 而 `@media` **不增加权重**：同选择器、同属性时，胜负**纯粹是源序**。
//
// ## 实例（迁移前就存在，计划 §4.7）
//
// `markdown.css` 768px 档的 `.markdown-body .katex { font-size: 1em }` 与
// `markdown-extras.css` 顶层的 `1.1em` 权重同为 (0,2,0)，后者在产物里更靠后
// ⇒ 那条窄屏字号**从未生效**。
//
// ## 判据与"响亮失败"
//
// 报"媒体查询那条在源序上更靠前"的每一对；**候选对为 0 就报错**
// ——那意味着这项检查没在扫描任何东西（前几轮的"空检查"事故就是这么来的）。
// 今天的发现逐条登记在 `MEDIA_WAR_RULES`：登记一条就说明"这条是已知的、
// 有意留着的"；**没登记的发现 = 红灯**，必须有人去看。
// 采集与求解用的是 `lib/css-cascade.mjs`（与下面的简写检查同一份实现）。
const MEDIA_WAR_RULES = [
  {
    id: 'katex 窄屏字号（已知：迁移前就从未生效）',
    match: (f) =>
      f.sel === '.markdown-body .katex' &&
      f.prop === 'font-size' &&
      f.media.context === '@media (max-width: 768px)' &&
      f.media.val === '1em' &&
      f.top.val === '1.1em',
    why:
      '`markdown.css` 的 768px 档想在小屏把公式字号收一档，但 `markdown-extras.css` 的顶层 ' +
      '`1.1em` 在产物里更靠后、权重相同 ⇒ 这条窄屏规则从未生效（计划 §4.7 / 规范 §7 盲区 4）。' +
      '**不要顺手"修好"它**：让它生效是改外观，得单开一轮带截图对比。' +
      '登记在这里的意思是"已量过、已知、故意留着"。',
  },
]

console.log('\n跨媒体查询覆盖战（同选择器 + 同属性，`@media` 里那条被顶层压掉）：')
const mediaWar = findMediaWars(cascadeDecls)
if (mediaWar.candidates === 0) {
  failed = true
  console.log(
    '   ✗ 候选对 0 条 —— 这项检查**没在扫描任何东西**。产物里应当存在大量' +
      '"同选择器同属性、一条在 @media 里一条在顶层"的对（今天的实测是几十对）；' +
      '为 0 说明解析或采集坏了，不能当成"没有覆盖战"。',
  )
} else {
  console.log(
    `   · 候选 ${mediaWar.candidates} 对（同选择器 + 同属性、两侧值不同、一条在 @media 里）；` +
      `其中媒体查询那条**在源序上输掉**的 ${mediaWar.findings.length} 条`,
  )
}
for (const f of mediaWar.findings) {
  console.log(`   ${MEDIA_WAR_RULES.some((r) => r.match(f)) ? '·' : '✗'} ${f.sel} { ${f.prop} }`)
  console.log(
    `       @media 那条：${f.media.context} 值 ${f.media.val}（${f.media.file} @${f.media.order}）`,
  )
  console.log(
    `       顶层那条  ：值 ${f.top.val}（${f.top.file} @${f.top.order}）  ⇒ 得主 ${f.winner}` +
      (f.winner === 'unknown' ? '（两个懒加载 chunk 之间静态判不了先后）' : ''),
  )
}
{
  const matched = new Set()
  for (const rule of MEDIA_WAR_RULES) {
    const hits = mediaWar.findings.filter(rule.match)
    if (hits.length === 0) {
      failed = true
      console.log(`   ✗ 注册的「${rule.id}」一条都没命中 —— 空注册（判据写错，或那条规则已经改了）`)
      continue
    }
    hits.forEach((h) => matched.add(h))
    console.log(`   ✓ 已登记：${rule.id}（命中 ${hits.length} 条）—— ${rule.why}`)
  }
  const unregistered = mediaWar.findings.filter((f) => !matched.has(f))
  if (unregistered.length) {
    failed = true
    console.log(`   ✗ 未登记的覆盖战 ${unregistered.length} 条（**新的**，必须有人决定是修还是登记）：`)
    for (const f of unregistered) console.log(`      - ${f.sel} { ${f.prop} }：${f.media.val} → ${f.top.val}`)
  }
}

// ── 3d. 简写 vs 长写竞争（收尾轮新增的检查之二，计划 §5 雷区 12）──
//
// ## 为什么既有三道检查都看不见它
//
// 差集按 `(上下文 + 选择器 + 属性)` 建键、冲突统计与 `CASCADE_PAIRS` /
// `ORDER_PAIRS` 按**属性名**配对 —— 而 `border`（简写，会展开出
// `border-left-*` 三条长写）与 `border-left`（长写）是**两个名字**，
// 选择器往往也不同，三道检查一处都配不上。
//
// ## 两半分开做，因为它们能提供的证据不一样
//
// 1. **同选择器那一半**（`SHORTHAND_CLASH_RULES`）：读源码就能看见顺序，
//    所以这一半的价值是"逐条登记谁赢、被压掉的那条是不是有意为之"；
//    注册表按**属性模式**登记（例如"`background` 简写 + `background-clip` 长写"
//    是渐变文字那套写法），因此**同模式的新声明不会报红**（这条限制写在
//    规范 §7 的盲区一节）。
// 2. **跨选择器那一半**（`CROSS_CLASS_SHORTHAND_RULES`）：这是真正看不见的那种
//    ——`.card` 与 `.qaAiCard` 并列写在同一个元素上
//    （`` className={`card ${styles.qaAiCard}`} ``），谁赢只看**产物里的先后**。
//    "两个类命中同一个元素"这件事由 TSX 的 `className` 提供证据
//    （`lib/css-cascade.mjs` 的 `coAppliedClassGroups`），注册表按**类名对**登记，
//    所以**新的类名对一定报红**。
//
// ## 响亮失败（四道）
//
// ① 同选择器候选对为 0 → 报错；② 跨选择器候选对为 0 → 报错；
// ③ TSX 里抽不到任何"并列 ≥2 个类名"的 `className` → 报错（抽取器失明）；
// ④ 注册项一条都没命中 → 报错。缺任何一道，这项检查都可能变成一条永远绿灯。
const SHORTHAND_CLASH_RULES = [
  {
    id: '渐变文字三件套：`background` 简写 + `background-clip: text`',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'background' && f.long.prop === 'background-clip',
    why:
      '`background` 简写会把 `background-clip` 重置回 `border-box`，所以"渐变文字"必须写成' +
      '`background` 在前、`background-clip: text` 在后（本项目 6 处 + KaTeX/评分卡各 1 处）。' +
      '顺序反了渐变就整块铺满元素、文字看不见 —— 而这条顺序在产物里逐字保留，本检查就是在钉它。',
  },
  {
    id: '渐变文字配 `background-color: transparent`',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'background' && f.long.prop === 'background-color',
    why: '同上那套写法（`-webkit-text-fill-color: transparent` 的兜底），顺序同样不能反。',
  },
  {
    id: '进度条流光：`background` 简写 + `background-size`',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'background' && f.long.prop === 'background-size',
    why: '`shimmer` 动画靠 `background-size: 200% 100%` 移动渐变；被简写重置就没有流光。',
  },
  {
    id: '先给简写、再单独改一边（padding）',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'padding' && f.winner === 'longhand',
    why:
      '`padding: …` 之后紧跟一条 `padding-top` / `padding-left` / `padding-bottom` 覆盖某一边 ——' +
      '同一规则内声明顺序固定，长写在后面赢。这是**有意**的写法（`.markdown-body blockquote` /' +
      '`._appLayout .container` 的安全区内边距 / 图谱侧栏）。',
  },
  {
    id: '窄屏 `padding` 简写压掉 `padding-left/right` 长写（赢家是简写）',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'padding' && f.winner === 'shorthand',
    why:
      '`learning.css` 的 768px 档里，`responsive.css` 搬来的 `padding-left/right: var(--space-md)`' +
      '在前、`refinements.css` 搬来的 `padding: var(--space-xs) var(--space-sm)` 在后 ⇒ **简写赢**。' +
      '序 13 搬家时保持了"长写在前、简写在后"，这条顺序就是行为（探针实测过，见证据 5.6-12）。',
  },
  {
    id: 'Loader 转圈：`border` 简写 + 某一边的 `border-*-color` 长写',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'border' && /^border-.*-color$/.test(f.long.prop),
    why: '转圈的"缺口"就是靠事后改一边的颜色做出来的（`.spinner` / `._graphSearchSpinner`）。',
  },
  {
    id: '`text-decoration` 简写 + `text-decoration-*` 长写',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'text-decoration',
    why: '批注下划线要指定颜色与粗细，简写（`underline`）在前、长写在后，顺序不能反。',
  },
  {
    id: '`border` 简写 + 更窄的简写（`border-left` / `border-top`）',
    match: (f) =>
      f.kind === 'same-selector' &&
      f.short.prop === 'border' &&
      /^border-(left|right|top|bottom)$/.test(f.long.prop),
    why:
      '答案卡左边加粗竖线、评分卡顶部彩条都是这个写法。**这正是雷区 12 的形态**，' +
      '只不过这里两半在同一个选择器里（顺序可读）；跨选择器的版本见下面那份注册表。',
  },
  {
    id: '`border-color` 简写 + `border-left-color` 长写（hover 态）',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'border-color' && f.long.prop === 'border-left-color',
    why: '答案卡 hover 只改左边框颜色，长写在简写之后。',
  },
  {
    id: 'KaTeX 自己的 `font` 简写 + `line-height`',
    match: (f) => f.kind === 'same-selector' && f.short.prop === 'font' && f.long.prop === 'line-height',
    why: '第三方（KaTeX 的 CSS）就是这么写的；它不在我们的改动范围里，登记是为了"已知"。',
  },
]

const CROSS_CLASS_SHORTHAND_RULES = [
  {
    id: 'card × card-accent-gold（`border` 简写 + `border-left` 长写）',
    match: (f) => pairIs(f, 'card', 'card-accent-gold'),
    why: '金色左边框要压掉 `.card` 的 1px 全边框；`card-accent-*` 在 `components.css` 里排在 `.card` 之后 ⇒ 长写赢。',
  },
  { id: 'card × card-accent-left', match: (f) => pairIs(f, 'card', 'card-accent-left'), why: '同上。' },
  { id: 'card × card-accent-error', match: (f) => pairIs(f, 'card', 'card-accent-error'), why: '同上。' },
  { id: 'card × card-accent-warning', match: (f) => pairIs(f, 'card', 'card-accent-warning'), why: '同上。' },
  {
    id: 'card × _qaAiCard（雷区 12 的原始实例）',
    match: (f) => pairIs(f, 'card', '_qaAiCard'),
    why:
      '`QA.tsx` 的 `` className={`card ${styles.qaAiCard}`} ``：全局 `.card` 写 `border: 1px solid`（简写），' +
      '模块 `.qaAiCard` 写 `border-left: 3px solid`（长写）。权重同为 (0,1,0)，谁赢只看先后 ——' +
      'QA 是懒加载 chunk，其 CSS 由 `__vitePreload` 在 `index.css` **之后**注入 ⇒ 长写赢（金色左边框）。' +
      '第三批之前这条只有一次性 jsdom 探针 + 文档守着，现在是常设检查。',
  },
  {
    id: 'filter-pill × filter-pill-active（`border` 简写 + `border-color` 长写）',
    match: (f) => pairIs(f, 'filter-pill', 'filter-pill-active'),
    why: '选中态只换边框颜色；`.filter-pill-active` 排在 `.filter-pill` 之后。',
  },
  {
    id: 'note-select-card × note-select-card-checked',
    match: (f) => pairIs(f, 'note-select-card', 'note-select-card-checked'),
    why: '学习评估页的勾选态：`border` 简写与 `border-color` / `border-left-width` 长写各一对，长写赢。',
  },
  {
    id: '_quizOption × _quizOptionSelected',
    match: (f) => pairIs(f, '_quizOption', '_quizOptionSelected'),
    why: '答题卡选中态换边框色（模块内两条规则，长写在后）。',
  },
  {
    id: '_graphBtn × _graphBtnActive',
    match: (f) => pairIs(f, '_graphBtn', '_graphBtnActive'),
    why: '图谱工具栏的激活态换边框色；`Graph.module.css` 里 active 排在后（`ORDER_PAIRS` 另钉了 hover 那一条）。',
  },
  {
    id: '_uploadZone × _uploadZoneActive',
    match: (f) => pairIs(f, '_uploadZone', '_uploadZoneActive'),
    why: '拖拽悬停态换成强调色虚线；模块内 active 排在后。',
  },
]

/**
 * 注册表的双向自检：每个注册项至少命中一条发现，每条发现至少被一个注册项命中。
 * 两个方向都要报错 —— 只查一个方向的话，"注册表写空"或"新竞争悄悄出现"都能溜过去。
 */
function checkClashRegistry(label, findings, rules) {
  const matched = new Set()
  let stale = 0
  for (const rule of rules) {
    const hits = findings.filter(rule.match)
    if (hits.length === 0) {
      stale += 1
      failed = true
      console.log(`   ✗ ${label}「${rule.id}」一条都没命中 —— 空注册（判据写错，或那条规则已经改了）`)
      continue
    }
    hits.forEach((h) => matched.add(h))
  }
  const unregistered = findings.filter((f) => !matched.has(f))
  if (unregistered.length) {
    failed = true
    console.log(`   ✗ ${label}未登记 ${unregistered.length} 条（**新的**竞争，必须有人决定是修还是登记）：`)
    for (const f of unregistered) {
      console.log(
        `      - ${f.subject}${f.context ? ` ${f.context}` : ''}：${f.short.prop}（简写）vs ${f.long.prop}（长写）` +
          ` → 得主 ${f.winner}`,
      )
    }
  }
  if (!stale && !unregistered.length) {
    console.log(`   ✓ ${label}${rules.length} 条注册全部命中，今天的 ${findings.length} 条发现全部已登记（双向自检通过）`)
  }
  return matched.size
}

/** 跨选择器的发现按（类名对 + 交叠 + 两侧属性 + 得主）去重，只保留出现位置清单 */
function dedupeShorthandFindings(findings) {
  const byKey = new Map()
  for (const f of findings) {
    const kind = f.classes ? 'cross-class' : 'same-selector'
    const subject = f.classes ? f.classes.join(' × ') : f.sel
    const key = `${kind}|${subject}|${f.context || ''}|${f.longhand}|${f.shorthand.prop}|${f.long.prop}|${f.winner}`
    if (!byKey.has(key)) {
      byKey.set(key, {
        kind,
        subject,
        classes: f.classes,
        context: f.context || '',
        overlap: f.longhand,
        short: f.shorthand,
        long: f.long,
        winner: f.winner,
        wheres: [],
      })
    }
    if (f.where) byKey.get(key).wheres.push(f.where)
  }
  return [...byKey.values()]
}

/**
 * 类名对的比较**不看顺序**：`className` 里哪个写在前面不影响它是同一对，
 * 而"注册表写反了顺序就永远绿灯"正是那种最难发现的空检查。
 */
const pairIs = (f, a, b) =>
  Array.isArray(f.classes) && [...f.classes].sort().join('|') === [a, b].sort().join('|')

// ── 3d-1. 同选择器 ──
console.log('\n简写 vs 长写（同选择器 + 同上下文，顺序即行为）：')
const sameSelectorClashes = findShorthandClashes(cascadeDecls)
if (sameSelectorClashes.candidates === 0) {
  failed = true
  console.log('   ✗ 候选对 0 条 —— 这项检查**没在扫描任何东西**（今天的产物里就有二十几对），不能当成"没有竞争"。')
} else {
  const deduped = dedupeShorthandFindings(sameSelectorClashes.findings)
  console.log(
    `   · 候选 ${sameSelectorClashes.candidates} 对（同选择器同上下文里"简写与长写落在同一批长写属性上"）；` +
      `去重后 ${deduped.length} 条`,
  )
  for (const f of deduped) {
    console.log(
      `   ${SHORTHAND_CLASH_RULES.some((r) => r.match(f)) ? '·' : '✗'} ${f.subject} ${f.context || '(顶层)'}` +
        ` :: ${f.overlap}`,
    )
    console.log(
      `       简写 ${f.short.prop}: ${f.short.val}（${f.short.file} @${f.short.order}）` +
        `  vs  长写 ${f.long.prop}: ${f.long.val}（${f.long.file} @${f.long.order}）  ⇒ 得主 ${f.winner}`,
    )
  }
  checkClashRegistry('同选择器注册表：', deduped, SHORTHAND_CLASH_RULES)
}

// ── 3d-2. 跨选择器（证据来自 TSX 的 className）──
console.log('\n简写 vs 长写（跨选择器：TSX 证明两个类名并列在同一个元素上）：')
;(function crossClassCheck() {
  const srcTsx = []
  ;(function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name)
      if (e.isDirectory()) walk(p)
      else if (e.name.endsWith('.tsx')) {
        srcTsx.push({ file: path.relative(process.cwd(), p).replace(/\\/g, '/'), text: fs.readFileSync(p, 'utf8') })
      }
    }
  })(path.join(process.cwd(), 'src'))
  const coGroups = coAppliedClassGroups(srcTsx)
  if (coGroups.length === 0) {
    failed = true
    console.log('   ✗ TSX 里一个"并列 ≥2 个类名"的 className 都没抽到 —— 抽取器失明，这项检查等于不存在。')
    return
  }
  const singleClassKeys = new Set(
    cascadeDecls.filter((d) => d.context === '' && isSingleClassSelector(d.sel)).map((d) => d.sel.replace(/^\./, '')),
  )
  if (singleClassKeys.size === 0) {
    failed = true
    console.log('   ✗ 产物里一个"单类规则"都没有 —— 类名表是空的，这项检查等于不存在。')
    return
  }
  const lookup = (ref) => {
    const key = ref.kind === 'module' ? `_${ref.name}` : ref.name
    return singleClassKeys.has(key) ? key : null
  }
  const cross = findCrossClassShorthandClashes(cascadeDecls, coGroups, lookup)
  const deduped = dedupeShorthandFindings(cross.findings)
  console.log(
    `   · TSX 里并列 ≥2 个类名的 className ${coGroups.length} 处；` +
      `两侧都是"单类规则"且落在同一批长写属性上的候选 ${cross.candidates} 对；去重后 ${deduped.length} 条`,
  )
  for (const f of deduped) {
    console.log(`   ${CROSS_CLASS_SHORTHAND_RULES.some((r) => r.match(f)) ? '·' : '✗'} ${f.subject} :: ${f.overlap}`)
    console.log(
      `       简写 ${f.short.prop}: ${f.short.val}（${f.short.file} @${f.short.order}）` +
        `  vs  长写 ${f.long.prop}: ${f.long.val}（${f.long.file} @${f.long.order}）  ⇒ 得主 ${f.winner}`,
    )
    if (f.wheres.length) console.log(`       出现位置：${[...new Set(f.wheres)].slice(0, 4).join(' / ')}`)
  }
  if (deduped.length === 0) {
    failed = true
    console.log(
      '   ✗ 跨选择器的候选 0 条 —— 今天产物里至少有 `card × _qaAiCard`（雷区 12 的实例）等十来对，' +
        '为 0 说明"TSX 抽取 → 产物类名解析"这条链断了，不能当成"没有竞争"。',
    )
  }
  checkClashRegistry('跨选择器注册表：', deduped, CROSS_CLASS_SHORTHAND_RULES)
})()

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
  // `cleaningProgress` / `cleaningProgressBar` / `cleaningPulse` 三个标记在收尾轮移除：
  // 那两条规则零引用（没有任何 TSX 挂它们），连同只被它们引用的 `cleaningPulse`
  // 一起在死代码清理里删掉了 —— 留在这里会让这项检查**永远红灯**，
  // 而"哪些东西被删了、凭什么"登记在 `CLEANUP_RETIREMENTS` 里（见文件末尾）。
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
  // 第二批：markdown-extras —— AI 提问浮层
  'askAiPanel',
  'askAiHeader',
  'askAiTitle',
  'askAiClose',
  'askAiBody',
  'askAiInput',
  'askAiSelectedHint',
  'askAiActions',
  'askAiQuestion',
  'askAiThinking',
  'askAiAnswer',
  'askAiError',
  'askAiProvider',
  // 第二批：批注操作浮层
  'selectionMenu',
  // 第三批：QA 页（提问气泡 + AI 卡片，动画随规则改名搬进模块）
  'qaUserBubble',
  'qaAiCard',
  'qaSlideUp',
  // 第三批：上传页（拖拽区 + 呼吸光晕动画）
  'uploadZone',
  'uploadZoneActive',
  'uploadGlowPulse',
  // 第三批：笔记列表页（工具条 + 搜索框；含从 responsive.css 搬来的 480px 两条）
  'listToolbar',
  'searchInputWrapper',
  'searchInputIcon',
  // 第三批：统计卡片（dashboard.css 余下部分 → components/StatCard）
  'statCard',
  'statCardBlue',
  'statCardGreen',
  'statCardGold',
  'statCardPurple',
  'statNumber',
  'statLabel',
  // 第四批（序 5）：学习评估页私有部分（assessment.css + refinements.css + responsive.css）
  'scoreBar',
  'scoreBarHeader',
  'scoreBarLabel',
  'scoreBarValue',
  'scoreBarTrack',
  'scoreBarFill',
  'scoreBarFillHigh',
  'scoreBarFillMid',
  'scoreBarFillLow',
  'quizQuestionCard',
  'quizQuestionCardActive',
  'quizQuestionNumber',
  'quizQuestionText',
  'scoreSummaryCard',
  'scoreSummaryNumber',
  'scoreSummaryLabel',
  'scoreValue',
  'knowledgePointsGrid',
  'knowledgePointsSection',
  // 第五批（序 8）：页面级布局挂点进模块（含 768px/480px 窄屏规则）
  'noteDetailHeader',
  'noteDetailActions',
  'noteListItem',
  'noteListActions',
  'editSplit',
  // 第六批（序 9）：侧边栏 / 抽屉 / 应用骨架（含窄屏规则与遮罩动画）
  'sidebarCollapsed',
  'sidebarMobileOpen',
  'sidebarHeader',
  'sidebarLogo',
  'sidebarCollapseBtn',
  'sidebarMobileClose',
  'sidebarBody',
  'sidebarOpenLock',
  'sidebarSection',
  'sidebarSectionTitle',
  'sidebarItemRow',
  'sidebarItem',
  'sidebarItemActive',
  'sidebarItemIcon',
  'sidebarItemLabel',
  'sidebarItemAction',
  'sidebarDivider',
  'sidebarFooter',
  'sidebarOverlay',
  'sidebarOverlayFadeIn',
  'appLayout',
  'appLayoutCollapsed',
  'sidebarMobileToggle',
  // 第七批（序 10）：图谱功能（graph.css 的 47 个类名 + graphSpin 动画）
  'graphPage',
  'graphPageMain',
  'graphSidebar',
  'graphCanvas',
  'graphToolbar',
  'graphToolbarLeft',
  'graphToolbarRight',
  'graphLegend',
  'graphLegendItem',
  'graphLegendItemActive',
  'graphLegendDot',
  'graphBtn',
  'graphBtnActive',
  'graphBadge',
  'graphCreateHint',
  'graphPanel',
  'graphPanelTitle',
  'graphSuggestionCard',
  'graphRelationLine',
  'graphControls',
  'graphControlBtn',
  'graphMinimap',
  'graphSuggestionScoreBar',
  'graphSuggestionScoreBarFill',
  'graphStatsGrid',
  'graphStatItem',
  'graphStatValue',
  'graphStatLabel',
  'graphStatsBarRow',
  'graphStatsBarLabel',
  'graphStatsBarTrack',
  'graphStatsBarFill',
  'graphStatsBarCount',
  'graphSearchBox',
  'graphSearchInput',
  'graphSearchSpinner',
  'graphSearchResults',
  'graphSearchResultItem',
  'graphSearchResultDot',
  'graphSearchResultTitle',
  'graphSearchResultType',
  'graphFilterSelect',
  'graphNeighborItem',
  'graphNeighborDot',
  'graphNeighborTitle',
  'graphNeighborRel',
  'graphNeighborCount',
  'graphSpin',
  // 序 6 收尾：`markdown.css` 最后 5 条 `.adhd-*` 规则进 `hooks/useAdhdReader.module.css`。
  // 四个类名由该模块导出、由 hook 用 `styles.*` 写入（字面量已 0 处）。
  // 留在全局的 `.markdown-body` / `.markdown-body …` 那一整组**不列** ——
  // 它们必须继续以 kebab 形态出现在产物里（三个不相邻功能在用 + `marked` 生成的 DOM）。
  'adhdReaderActive',
  'adhdBlock',
  'adhdCurrentBlock',
  'adhdLineMarker',
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
  // 第二批：markdown-extras（搬走 20 条，留下的是 KaTeX / 批注那些
  // "没有组件可挂类名"的规则 —— 它们**必须**继续以 kebab 形态出现在产物里，
  // 所以不列进这份退休名单）
  'ask-ai-panel',
  'ask-ai-header',
  'ask-ai-title',
  'ask-ai-close',
  'ask-ai-body',
  'ask-ai-input',
  'ask-ai-selected-hint',
  'ask-ai-actions',
  'ask-ai-question',
  'ask-ai-thinking',
  'ask-ai-answer',
  'ask-ai-error',
  'ask-ai-provider',
  'selection-menu',
  // 第三批：learning.css 的三组（各只有 1 个消费者）+ responsive.css 里
  // **只定义在补丁层**的 `.list-toolbar`；以及 dashboard.css 余下的统计卡片组。
  // 同表里留在全局的 `.filter-pill*` / `.segment-*` / `.collapse-arrow*` /
  // `.state-*` / `.spinner`，以及 `dashboard.css` 的 `.progress-bar*`
  // **必须**继续以 kebab 形态出现，所以不列进来。
  'qa-user-bubble',
  'qa-ai-card',
  'upload-zone',
  'upload-zone-active',
  'list-toolbar',
  'search-input-wrapper',
  'search-input-icon',
  'stat-card',
  'stat-card-blue',
  'stat-card-green',
  'stat-card-gold',
  'stat-card-purple',
  'stat-number',
  'stat-label',
  // 第四批（序 5）：学习评估页搬进模块的 19 个类名。
  // 留在全局的 `.assessment-header` / `-title` / `-subtitle` 与 `.note-select-card*`
  // **必须**继续以 kebab 形态出现在产物里（学习评估页 + 项目页共用），
  // 所以不列进这份退休名单。`score-bar` 这类前缀名靠 `(?![\w-])` 边界
  // 与 `score-bar-fill` 区分开，两者都列也不会互相误判。
  'score-bar',
  'score-bar-header',
  'score-bar-label',
  'score-bar-value',
  'score-bar-track',
  'score-bar-fill',
  'score-bar-fill-high',
  'score-bar-fill-mid',
  'score-bar-fill-low',
  'quiz-question-card',
  'quiz-question-card-active',
  'quiz-question-number',
  'quiz-question-text',
  'score-summary-card',
  'score-summary-number',
  'score-summary-label',
  'score-value',
  'knowledge-points-grid',
  'knowledge-points-section',
  // 第五批（序 8）：三个组件各带自己的窄屏规则一起进模块。
  // `responsive.css` 里对应位置留了注释（"已随组件搬走"），不留选择器。
  'note-detail-header',
  'note-detail-actions',
  'note-list-item',
  'note-list-actions',
  'edit-split',
  // 第六批（序 9）：侧边栏整节 + 应用骨架。`.navbar*`（旧顶部导航栏）**不列在这里** ——
  // 它在收尾轮被删除了（零引用），由文件末尾的 `CLEANUP_RETIREMENTS` 用
  // "迁移前有 → 源码没了 → 产物没了"三个方向自检，比这份名单更严。
  'sidebar',
  'sidebar-collapsed',
  'sidebar-mobile-open',
  'sidebar-header',
  'sidebar-logo',
  'sidebar-collapse-btn',
  'sidebar-mobile-close',
  'sidebar-body',
  'sidebar-open-lock',
  'sidebar-section',
  'sidebar-section-title',
  'sidebar-item-row',
  'sidebar-item',
  'sidebar-item-active',
  'sidebar-item-icon',
  'sidebar-item-label',
  'sidebar-item-action',
  'sidebar-divider',
  'sidebar-footer',
  'sidebar-overlay',
  'app-layout',
  'app-layout-collapsed',
  'sidebar-mobile-toggle',
  // 第七批（序 10）：图谱的 47 个类名。`graph.css` 里那个 `@keyframes graph-spin`
  // 不带类名，不在退休名单的范围内 —— 它在收尾轮被删除，由 `CLEANUP_RETIREMENTS` 自检。
  'graph-page',
  'graph-page-main',
  'graph-sidebar',
  'graph-canvas',
  'graph-toolbar',
  'graph-toolbar-left',
  'graph-toolbar-right',
  'graph-legend',
  'graph-legend-item',
  'graph-legend-item-active',
  'graph-legend-dot',
  'graph-btn',
  'graph-btn-active',
  'graph-badge',
  'graph-create-hint',
  'graph-panel',
  'graph-panel-title',
  'graph-suggestion-card',
  'graph-relation-line',
  'graph-controls',
  'graph-control-btn',
  'graph-minimap',
  'graph-suggestion-score-bar',
  'graph-suggestion-score-bar-fill',
  'graph-stats-grid',
  'graph-stat-item',
  'graph-stat-value',
  'graph-stat-label',
  'graph-stats-bar-row',
  'graph-stats-bar-label',
  'graph-stats-bar-track',
  'graph-stats-bar-fill',
  'graph-stats-bar-count',
  'graph-search-box',
  'graph-search-input',
  'graph-search-spinner',
  'graph-search-results',
  'graph-search-result-item',
  'graph-search-result-dot',
  'graph-search-result-title',
  'graph-search-result-type',
  'graph-filter-select',
  'graph-neighbor-item',
  'graph-neighbor-dot',
  'graph-neighbor-title',
  'graph-neighbor-rel',
  'graph-neighbor-count',
  // 序 6 收尾：`markdown.css` 最后 5 条 `.adhd-*` 规则搬进 hook 模块，
  // 四个 kebab 类名退休（产物里只应有 `._adhdBlock_hash` 这类 camelCase 形态）。
  // ⚠️ `.markdown-body` 与 `.markdown-body …` 那一整组**不列** ——
  // 它们按判据继续留全局，必须继续以 kebab 形态出现在产物里。
  'adhd-reader-active',
  'adhd-block',
  'adhd-current-block',
  'adhd-line-marker',
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

// ════════════════════════════════════════════════════════════════════
// ── 6. 收尾轮：死代码清理的**常设护栏** ──
//
// ## 为什么删除也要有护栏
//
// "删掉零引用的东西"这件事本身可能出错，而且错法有两种：
//   ① **删错了**（其实还有人用）—— 删除前的证据（grep + 运行时拼类名扫描）
//      是一次性劳动，落不进代码里；
//   ② **删对了但没删干净**（产物里还留着，或只删了定义没删引用）。
// 所以这里把"删了什么"登记成表，每条都做**三个方向**的自检：
//   a. **迁移前确实有它** —— 从 HEAD 往回按内容找"那个文件里还有这个名字"的
//      修订（与 `css-migration-diff.mjs` 的 `findRecentRev` 同一套办法；
//      写死 HEAD 会在删除提交之后失效）；
//   b. **现在源码里确实没有它** —— 扫 `src/**/*.css`（模块与全局样式表都算）；
//      有残留 ⇒ 说明删了一半；
//   c. **产物里确实没有它** —— 类名按 `.name(?![\w-])` 匹配、`@keyframes` 按
//      `@keyframes name` 匹配、令牌按 `--name` 匹配。
// 再加上"表不能为空"这一条，这套检查不可能退化成永远绿灯。
//
// 第三条（类名在产物里消失）与上面 `RETIRED` 有重叠但**不重复**：
// `RETIRED` 登记的是"迁移时退休的 kebab 类名"，这里是"收尾轮删掉的死代码"，
// 并且多了 a/b 两个方向 —— 而 a 正是"删除有据"的机器可验版本。
// ════════════════════════════════════════════════════════════════════
const CLEANUP_RETIREMENTS = [
  // ── 布局：旧顶部导航栏（收尾轮 · 第 1 组）──
  {
    kind: 'class',
    name: 'navbar',
    sheet: 'src/styles/layout.css',
    why: '旧顶部导航栏：TSX/TS 里 0 处引用、运行时拼类名 0 处；迁移前 `.navbar{display:none}` 就已经把它藏掉了',
  },
  { kind: 'class', name: 'navbar-logo', sheet: 'src/styles/layout.css', why: '同上（`.navbar-logo:hover` 与它共用类名）' },
  { kind: 'class', name: 'navbar-links', sheet: 'src/styles/layout.css', why: '同上' },
  { kind: 'class', name: 'navbar-logout', sheet: 'src/styles/layout.css', why: '同上（`.navbar-logout:hover` 与它共用类名）' },
  {
    kind: 'token',
    name: '--navbar-height',
    sheet: 'src/styles/base.css',
    why: '唯一读者是 `.navbar { height: var(--navbar-height) }`，随 `.navbar*` 同批删除',
  },
  // ── 零引用的预留语义化类（收尾轮 · 第 2 组）──
  {
    kind: 'class',
    name: 'link-modal',
    sheet: 'src/styles/refinements.css',
    why: '零引用预留类（`LinkManagerModal.tsx` 用的是内联 style，一个类名都没挂）',
  },
  { kind: 'class', name: 'material-list-item', sheet: 'src/styles/refinements.css', why: '同上（`:hover` 变体共用类名）' },
  { kind: 'class', name: 'material-list-item-selected', sheet: 'src/styles/refinements.css', why: '同上' },
  { kind: 'class', name: 'type-badge', sheet: 'src/styles/refinements.css', why: '同上' },
  { kind: 'class', name: 'type-badge-material', sheet: 'src/styles/refinements.css', why: '同上' },
  // ── 没有用户的 @keyframes（收尾轮 · 第 3 组）──
  {
    kind: 'keyframes',
    name: 'scaleIn',
    sheet: 'src/styles/base.css',
    why: '迁移时复制成模块里的 `authScaleIn` / `feedbackScaleIn` 后，全局这份再没有引用者',
  },
  {
    kind: 'keyframes',
    name: 'cleaning-pulse',
    sheet: 'src/styles/base.css',
    why: '复制成模块里的 `cleaningPulse`；收尾轮连那条死规则 `.cleaningProgressBar` 与模块内的 `cleaningPulse` 一起删了',
  },
  {
    kind: 'keyframes',
    name: 'glowPulse',
    sheet: 'src/styles/base.css',
    why: '复制成模块里的 `uploadGlowPulse` 后再没有引用者（计划 §7.0 第 5 条）',
  },
  {
    kind: 'keyframes',
    name: 'slideDown',
    sheet: 'src/styles/base.css',
    why: '从来没有过用户（全项目 grep 只命中定义本身）',
  },
  { kind: 'keyframes', name: 'pulse', sheet: 'src/styles/base.css', why: '同上' },
  { kind: 'keyframes', name: 'float', sheet: 'src/styles/base.css', why: '同上（`ProjectsErrorBanner.tsx` 里的 `float` 是 CSS 属性，不是动画）' },
  { kind: 'keyframes', name: 'gradientShift', sheet: 'src/styles/base.css', why: '同上' },
  {
    kind: 'keyframes',
    name: 'graph-spin',
    sheet: 'src/styles/graph.css',
    why: '复制成模块里的 `graphSpin` 后再没有引用者；它原来"不删"的第①条理由（动画体比对读 git HEAD 的原文）已改成按内容定位修订',
  },
  // ── 模块里的死规则（收尾轮 · 第 4 组）──
  {
    kind: 'class',
    name: 'cleaningProgress',
    sheet: 'src/components/CleaningPanel.module.css',
    why: '清洗进度条改由 `TaskProgress` 渲染（用全局 `.progress-bar*`），这条规则全项目 0 处 TSX 引用（第一批逐字搬来时就没用户）',
  },
  {
    kind: 'class',
    name: 'cleaningProgressBar',
    sheet: 'src/components/CleaningPanel.module.css',
    why: '同上，它是模块内 `@keyframes cleaningPulse` 的唯一引用者',
  },
  {
    kind: 'keyframes',
    name: 'cleaningPulse',
    sheet: 'src/components/CleaningPanel.module.css',
    why: '只被 `.cleaningProgressBar` 引用；那条规则删了它就没有用户',
  },
]

console.log('\n收尾轮死代码清理（三个方向逐条自检：迁移前有 → 源码里没了 → 产物里没了）：')
if (CLEANUP_RETIREMENTS.length === 0) {
  failed = true
  console.log('   ✗ 登记表为空 —— 这套检查等于不存在')
}
const cleanupSourceCache = new Map()
/** 从 HEAD 往回找"这个文件里还有这个名字"的修订（按内容定位，见文件头 a 条） */
function findCleanupRev(sheet, name, kind) {
  const cacheKey = `${sheet}::${kind}::${name}`
  if (cleanupSourceCache.has(cacheKey)) return cleanupSourceCache.get(cacheKey)
  const re =
    kind === 'keyframes' ? new RegExp(`@keyframes\\s+${name.replace(/[-]/g, '\\-')}\\s*\\{`) : new RegExp(`(\\.|--)?${name.replace(/[-]/g, '\\-')}(?![\\w-])`)
  const rev = findRecentRev((candidate) => re.test(readFromGit(path.join(process.cwd(), sheet), candidate)))
  const out = rev ? { rev, text: readFromGit(path.join(process.cwd(), sheet), rev) } : null
  cleanupSourceCache.set(cacheKey, out)
  return out
}
{
  let okCount = 0
  for (const item of CLEANUP_RETIREMENTS) {
    const srcAbs = path.join(process.cwd(), item.sheet)
    const problems = []
    // a. 迁移前确实有它
    const gitRev = findCleanupRev(item.sheet, item.name, item.kind)
    if (!gitRev) {
      problems.push('从 HEAD 往回 40 个提交里找不到"那个文件里还有它"的修订 ⇒ 名字或来源样式表写错了')
    }
    // b. 现在源码里确实没有它
    //    ⚠️ 比较前**必须剥注释**：这几条删除都在原处留了墓碑注释
    //    （写明"删了什么、凭什么"），注释里出现类名不是"还有人用它"。
    //    不剥的话这套自检会对自己的文档报红 —— 而"给删除留注释"正是本项目
    //    的既有做法（`auth.css` / `responsive.css` / `refinements.css` 都是）。
    const srcHits = []
    for (const abs of srcCssFiles) {
      const text = stripComments(fs.readFileSync(abs, 'utf8'))
      const re =
        item.kind === 'keyframes'
          ? new RegExp(`@keyframes\\s+${item.name.replace(/[-]/g, '\\-')}\\s*\\{`, 'g')
          : new RegExp(`(\\.|--)?${item.name.replace(/[-]/g, '\\-')}(?![\\w-])`, 'g')
      if (re.test(text)) srcHits.push(path.relative(process.cwd(), abs).replace(/\\/g, '/'))
    }
    if (srcHits.length) problems.push(`源码里还有：${[...new Set(srcHits)].join(', ')}`)
    // c. 产物里确实没有它
    const distRe =
      item.kind === 'keyframes'
        ? new RegExp(`@keyframes\\s+${item.name.replace(/[-]/g, '\\-')}(?![\\w-])`, 'g')
        : item.kind === 'token'
          ? new RegExp(`--${item.name.replace(/[-]/g, '\\-')}(?![\\w-])`, 'g')
          : new RegExp(`\\.${item.name.replace(/[-]/g, '\\-')}(?![\\w-])`, 'g')
    const distHits = (all.match(distRe) || []).length
    if (distHits) problems.push(`产物里还有 ${distHits} 次`)
    if (problems.length) {
      failed = true
      console.log(`   ✗ ${item.kind} \`${item.name}\`（${item.sheet}）：${problems.join('；')}`)
    } else {
      okCount += 1
      console.log(`   ✓ ${item.kind} \`${item.name}\`：迁移前在 ${item.sheet}（${gitRev.rev}）有，源码与产物都没有了 —— ${item.why}`)
    }
  }
  console.log(`   ${okCount}/${CLEANUP_RETIREMENTS.length} 条通过三个方向的自检`)
}

// ════════════════════════════════════════════════════════════════════
// ── 7. 产物里"定义了但没有任何引用"的 @keyframes ──
//
// 上面的第 1 项查的是"**引用**有没有定义"（悬空动画）；这一项查反方向：
// "**定义**有没有引用"。死动画正是收尾轮删掉的那一批（`scaleIn` /
// `cleaning-pulse` / `glowPulse` / `slideDown` / `pulse` / `float` /
// `gradientShift` / `graph-spin`），删完之后这条检查让它**不可能悄悄回来**。
//
// ⚠️ 有一个合法的例外：**只被 tsx 行内样式引用**的动画。
// 行内 `style={{ animation: 'shake …' }}` 不过 CSS Modules、也不进产物 CSS，
// 所以样式表里查不到引用者 —— 但它真的在用（`ErrorDisplay.tsx`）。
// 这类动画必须**点名登记**在 `INLINE_ONLY_KEYFRAMES` 里，
// 并且登记项本身也要自检：产物里必须有这个定义、且**样式表里确实没有**引用
// （否则说明它已经有样式表用户了，登记项过期 ⇒ 报错）。
// ════════════════════════════════════════════════════════════════════
const INLINE_ONLY_KEYFRAMES = [
  {
    name: 'shake',
    where: 'components/ErrorDisplay.tsx:12 的 style={{ animation: \'shake 0.4s ease, fadeIn …\' }}',
    why: '错误卡片的抖动只写在行内样式里；行内样式不过 CSS Modules，所以产物 CSS 里看不到引用者',
  },
]
console.log('\n产物里"定义了但样式表没人引用"的 @keyframes：')
{
  const definedAll = new Map() // name → [file]
  const usedAll = new Map() // name → [file]
  for (const s of sheets) {
    const css = clean(s.css)
    for (const m of css.matchAll(/@keyframes\s+([\w-]+)/g)) {
      if (!definedAll.has(m[1])) definedAll.set(m[1], [])
      definedAll.get(m[1]).push(s.name)
    }
    for (const m of css.matchAll(/(?:^|[;{])\s*animation(?:-name)?\s*:\s*([^;{}]+)/g)) {
      for (const name of animationNamesFrom(m[1])) {
        if (!usedAll.has(name)) usedAll.set(name, [])
        usedAll.get(name).push(s.name)
      }
    }
  }
  if (definedAll.size === 0) {
    failed = true
    console.log('   ✗ 产物里一个 @keyframes 都没有 —— 解析坏了，不能据此说"没有死动画"')
  }
  const inlineOnly = new Map(INLINE_ONLY_KEYFRAMES.map((k) => [k.name, k]))
  const dead = [...definedAll.keys()].filter((n) => !usedAll.has(n) && !inlineOnly.has(n))
  for (const n of dead) {
    failed = true
    console.log(`   ✗ ${n}（定义在 ${definedAll.get(n).join(', ')}）：没有任何 animation 引用它 —— 死动画`)
  }
  for (const k of INLINE_ONLY_KEYFRAMES) {
    const definedIn = definedAll.get(k.name)
    if (!definedIn) {
      failed = true
      console.log(`   ✗ 登记的"只有行内用户"的动画 \`${k.name}\` 在产物里没有定义 —— 登记项过期或写错了`)
      continue
    }
    if (usedAll.has(k.name)) {
      failed = true
      console.log(
        `   ✗ 登记的"只有行内用户"的动画 \`${k.name}\` 现在**有样式表引用**了（${usedAll.get(k.name).join(', ')}）` +
          ` —— 登记项过期，请把它从 INLINE_ONLY_KEYFRAMES 里删掉`,
      )
      continue
    }
    console.log(`   ✓ ${k.name}：产物里有定义（${definedIn.join(', ')}）、样式表里 0 引用，属登记的"仅行内使用"—— ${k.why}`)
  }
  console.log(
    `   ${dead.length === 0 ? '✓' : '✗'} 产物内 ${definedAll.size} 个 @keyframes：` +
      `${usedAll.size} 个有样式表引用 + ${INLINE_ONLY_KEYFRAMES.length} 个登记的"仅行内使用"，死动画 ${dead.length} 个`,
  )
}

process.exit(failed ? 1 : 0)
