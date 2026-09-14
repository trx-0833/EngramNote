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
 *    在产物里必须有对应的 `@keyframes`。
 * 2. **真正打架的重复定义**：同一个（媒体查询上下文 + 选择器 + **属性**）
 *    被赋了两个不同的值。这是"覆盖战争"的产物级指纹，也是唯一值得报的形态：
 *    同一个选择器分几条规则写**不同**属性（`.x{color}` + `.x{padding}`）
 *    是正常 CSS，不算冲突 —— 第一版没区分这两者，报了 69 条噪音。
 * 3. **迁移切片的选择器是否真的进了产物**（按哈希后的关键字确认）。
 * 4. **产物新鲜度**：产物 mtime 早于源码 mtime 就报警 —— 否则会拿旧产物
 *    得出"已经修好了"的结论（本轮真的踩到：构建失败、dist 还是上一版）。
 *
 * 用法：node scripts/verify-built-css.mjs [dist目录]
 */
import fs from 'node:fs'
import path from 'node:path'
import { parseRules, splitSelectors, stripComments } from './lib/css-parse.mjs'

/** 去掉注释（保留一个短别名，读起来比到处写 stripComments 顺） */
const clean = stripComments

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
const moduleFiles = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p)
    else if (e.name.endsWith('.css')) moduleFiles.push(p)
  }
})(path.join(process.cwd(), 'src'))
const newestSrc = Math.max(...moduleFiles.map((f) => fs.statSync(f).mtimeMs))
if (newestSrc > distMtime) {
  failed = true
  console.log(
    `✗ 产物比源码旧（源码最新 ${new Date(newestSrc).toISOString()} > 产物 ${new Date(distMtime).toISOString()}）：` +
      `先重新构建，否则下面的结论是拿旧产物得出的`,
  )
} else {
  console.log('✓ 产物比 src 下所有 CSS 都新（不是陈旧产物）')
}

// ── 1. @keyframes 定义 vs 动画引用 ──
const definedKeyframes = new Set()
for (const m of clean(all).matchAll(/@keyframes\s+([\w-]+)/g)) definedKeyframes.add(m[1])

/** 从 animation 简写里挑出动画名：跳过时长/曲线/计数/关键字等非标识符位置 */
const NOT_A_NAME =
  /^(none|infinite|linear|ease|ease-in|ease-out|ease-in-out|alternate|alternate-reverse|reverse|forwards|backwards|both|normal|running|paused|initial|inherit|unset|revert|steps|linear\(.*\)|cubic-bezier\(.*\))$/

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

const referenced = new Map()
for (const s of sheets) {
  for (const m of clean(s.css).matchAll(/(?:^|[;{])\s*animation(?:-name)?\s*:\s*([^;{}]+)/g)) {
    for (const name of animationNamesFrom(m[1])) {
      if (!referenced.has(name)) referenced.set(name, new Set())
      referenced.get(name).add(s.name)
    }
  }
}
const dangling = [...referenced.entries()].filter(([n]) => !definedKeyframes.has(n))
if (dangling.length) {
  failed = true
  console.log(`\n✗ 悬空的动画引用（引用了产物中不存在的 @keyframes）：${dangling.length}`)
  for (const [n, files] of dangling) console.log(`   - ${n}  <- ${[...files].join(', ')}`)
} else {
  console.log(
    `✓ 动画引用全部有定义（引用 ${referenced.size} 个名字，产物内 @keyframes 定义 ${definedKeyframes.size} 个）`,
  )
}

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
    const dm = declsOf(r)
    for (const sel of splitSelectors(r.selector)) {
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
// 而那正是下一轮要排的雷。所以直接回源文件里找：哪个 src/styles/*.css
// 定义了这条选择器，就把源文件名附上。
const srcStylesDir = path.join(process.cwd(), 'src/styles')
const srcSheets = fs.existsSync(srcStylesDir)
  ? fs
      .readdirSync(srcStylesDir)
      .filter((f) => f.endsWith('.css'))
      .map((f) => ({ name: f, rules: parseRules(fs.readFileSync(path.join(srcStylesDir, f), 'utf8')) }))
  : []

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
// 归因：本轮迁移只动了 learning.css / responsive.css 与两个新模块
// （QuizAnswerCard.module.css / SelfRatingButtons.module.css），
// 别处冒出来的冲突是迁移前就存在的债，不能算到这次改动头上。
// 判据不看产物文件名（打包后都叫 index.css），而是回源文件里找这条选择器
// 定义在哪个 src/styles/*.css。
const TOUCHED_BY_THIS_MIGRATION = new Set([
  'learning.css',
  'responsive.css',
  'QuizAnswerCard.module.css',
  'SelfRatingButtons.module.css',
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

// ── 3. 迁移切片是否真的进产物 ──
const SLICE_MARKERS = [
  'quizOption',
  'quizOptionSelected',
  'feedbackCorrect',
  'feedbackIncorrect',
  'feedbackScaleIn',
  'feedbackShake',
  'selfRatingBtn',
]
console.log('\n迁移切片类名/动画在产物中的出现次数：')
for (const marker of SLICE_MARKERS) {
  const n = (all.match(new RegExp(marker, 'g')) || []).length
  if (n === 0) failed = true
  console.log(`   ${n === 0 ? '✗' : '✓'} ${marker}: ${n}`)
}

// ── 4. 迁移前的全局类名不得再被组件使用（否则规则搬走了、引用还在）──
const RETIRED = ['quiz-option', 'quiz-option-selected', 'feedback-correct', 'feedback-incorrect', 'feedback-pending', 'self-rating-btn']
console.log('\n已退休的全局类名（在产物 CSS 里应当彻底消失）：')
for (const r of RETIRED) {
  // 用边界匹配，避免把 `.quiz-option-editor` 之类误判
  const hits = [...all.matchAll(new RegExp(`\\.${r.replace(/-/g, '\\-')}(?![\\w-])`, 'g'))].length
  if (hits > 0) failed = true
  console.log(`   ${hits === 0 ? '✓' : '✗'} .${r}: ${hits}`)
}

process.exit(failed ? 1 : 0)