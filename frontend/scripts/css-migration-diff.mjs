/**
 * 迁移前后规则清单差集（overhaul-plan 5.6 的"什么都没丢"证据）
 *
 * ## 它回答的问题
 *
 * "把规则从全局样式表搬进 CSS Modules 之后，有没有哪一条**悄悄变了**？"
 * 读代码回答不了：类名被哈希、选择器被重写、声明会被压缩器重排。
 * 所以这里把"迁移前（HEAD 里的源码）"与"迁移后（dist 产物）"两侧的规则
 * 归一化成同一形状再逐条比：
 *
 *   - 侧别 A：`git show HEAD:...` 里的原规则；
 *   - 侧别 B：dist 产物里哈希后的规则；
 *   - 归一化：剥哈希后缀、统一值写法、类名映射成中性占位符 `.X`；
 *   - 对齐：A 有 B 无 = **丢失**（逐条交代）；B 有 A 无 = **新增**（同样交代）；
 *     两边都有但值不同 = **值有变化**（必须能说清为什么）。
 *
 * ## 为什么"值有变化"不等于事故
 *
 * 本轮有两处**有意**的值替换，都在这里显式列出来：
 *   1. `box-shadow: 0 0 0 3px rgba(15,52,96,.08)` → `… var(--color-primary-light)`
 *      （令牌化，值完全相同，归一化后应判为"逐字保留"）；
 *   2. `animation: scaleIn …` → `animation: feedbackScaleIn …`
 *      + 模块内新增 `@keyframes feedbackScaleIn`（动画体逐字复制）。
 *      这是**必需**的：CSS Modules 会把动画名一起哈希，裸名引用会在产物里
 *      悬空（详见 QuizAnswerCard.module.css 文件头）。这里对动画做一次
 *      名称替换后再比，把"改名"与"动画体是否一致"分开看。
 *
 * 用法：node scripts/css-migration-diff.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { parseRules, classesOf } from './lib/css-parse.mjs'

const root = process.cwd()

/** 老类名 → 新模块类名 */
const RENAME = {
  'quiz-option': 'quizOption',
  'quiz-option-selected': 'quizOptionSelected',
  'feedback-correct': 'feedbackCorrect',
  'feedback-incorrect': 'feedbackIncorrect',
  'self-rating-btn': 'selfRatingBtn',
}

const WANTED_SELECTOR = /quiz-option|quiz-option-selected|feedback-correct|feedback-incorrect|self-rating-btn/i

/**
 * 剥掉 CSS Modules 的哈希后缀：`_quizOption_1a0d4_52` → `_quizOption`。
 * 哈希形如 `_<5位>_<行号>`，但**只剥这一种**，不要用宽泛的
 * `_[0-9a-z]+_\d+` —— 那会把 `_feedbackscalein_1a0d4_1` 里经过
 * 别名替换后的名字也啃掉一截，剩下一个孤零零的下划线，
 * 让两侧看起来"值不同"（本轮踩过，两条 animation 报了假警报）。
 */
const stripHash = (s) => s.replace(/_([0-9a-z]{5})_\d+(?![0-9a-z])/g, '')

/**
 * 值归一化：吃掉压缩器的等价改写，只留语义。
 *
 * 一个有据可查的等价替换：
 *   `rgba(15, 52, 96, .08)` ↔ `var(--color-primary-light)`。
 *   本轮的令牌化替换 —— base.css 里
 *   `--color-primary-light: rgba(15, 52, 96, 0.08)`，两者数值完全相同，
 *   所以把令牌展开回字面值再比，避免把它误报成"值有变化"。
 */
const TOKEN_EQUIV = {
  'var(--color-primary-light)': 'rgba(15,52,96,.08)',
}

function canonValue(v) {
  let s = v
    .replace(/\s+/g, ' ')
    .replace(/(?<![\d.])0\.(\d)/g, '.$1') // 0.3s → .3s
    .replace(/translateX\(([^)]*)\)/g, 'translate($1)')
    .replace(/translateY\(([^)]*)\)/g, 'translate(0, $1)')
    .replace(/\s*,\s*/g, ',')
    .replace(/\s+/g, '')
    .toLowerCase()
  for (const [token, literal] of Object.entries(TOKEN_EQUIV)) {
    s = s.split(token).join(literal)
  }
  return s
}

/**
 * 声明列表 → "prop:val" 排序后的数组。
 *
 * `animation` / `animation-name` 的值**整条归一成 `NAME`**，不参与文本比对：
 * 动画名在产物里被哈希（`scaleIn` → `_feedbackScaleIn_1a0d4_1`），
 * 逐字比文本必然不等。而动画真正要验的两件事 ——
 * "引用的动画在产物里有没有定义"（绑定）与"动画体是否与全局原版一致"——
 * 由文件末尾两个独立检查负责，比硬凑成一次字符串比对可靠得多
 * （试图在文本里改写哈希名时，我先后踩到前导下划线残留、
 * 重复替换成 `_feedbackfeedbackscalein`、负向后顾被 `_?` 绕过三种假警报）。
 *
 * ⚠️ 判断必须看**属性名**，不能对值写 `/animation:/` ——
 * 传进来的已经只是值（`feedbackScaleIn 0.3s …`），不含属性名，永远匹配不上。
 */
function canonDecls(decls) {
  return decls
    .map(([p, v]) => {
      const prop = p.trim().toLowerCase()
      if (prop === 'animation' || prop === 'animation-name') return `${prop}:NAME`
      return `${prop}:${canonValue(v)}`
    })
    .sort()
}

/**
 * 选择器 → 中性占位符。
 *
 * ⚠️ 每个类名必须映射到**不同**的占位符（`.C0` / `.C1` …），
 * 不能都换成同一个 `.X`：那样 `.quiz-option` / `.quiz-option-selected` /
 * `.feedback-correct` 会挤到同一个 key 上，查表时拿到的是别人那条规则，
 * 于是报出"值有变化"的假警报（本轮踩过：5 条规则被并成 1 个 key）。
 * 映射按 RENAME 的固有顺序，两侧一致，所以占位符含义稳定可比。
 *
 * 另注意产物里是 `._quizOption_hash`：点后面多一个下划线，
 * 所以 `\.` 后面要允许 `_?`，否则一条都对不上。
 */
const PLACEHOLDER = new Map(Object.keys(RENAME).map((name, i) => [name, `.C${i}`]))

function neutralSelector(sel) {
  let s = stripHash(sel)
  for (const [oldName, newName] of Object.entries(RENAME)) {
    const ph = PLACEHOLDER.get(oldName)
    s = s.replace(new RegExp(`\\._?${newName}(?![\\w-])`, 'g'), ph)
    s = s.replace(new RegExp(`\\._?${oldName}(?![\\w-])`, 'g'), ph)
  }
  return s.replace(/\s+/g, ' ').trim()
}

// ── 侧别 A：迁移前（HEAD） ──
const beforeRules = []
for (const rel of [
  'src/styles/learning.css',
  'src/styles/responsive.css',
]) {
  const abs = path.join(root, rel)
  // 侧别 A 用 git 读，这样"迁移前"是 HEAD 的真实内容，
  // 而不是某个人手工留存、可能已经过期的快照文件
  const { execFileSync } = await import('node:child_process')
  const repoRel = path.relative(path.join(root, '..'), abs).replace(/\\/g, '/')
  const css = execFileSync('git', ['show', `HEAD:${repoRel}`], { encoding: 'utf8' })
  for (const r of parseRules(css)) {
    if (WANTED_SELECTOR.test(r.selector)) {
      beforeRules.push({ side: `${rel}@HEAD`, context: r.context, selector: r.selector, decls: canonDecls(r.decls) })
    }
  }
}

// ── 侧别 B：迁移后（dist 产物） ──
const assetsDir = path.join(root, 'dist/assets')
const afterRules = []
const beforeMtime = Math.max(
  ...fs
    .readdirSync(path.join(root, 'src/styles'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => fs.statSync(path.join(root, 'src/styles', f)).mtimeMs),
)
for (const f of fs.readdirSync(assetsDir).filter((x) => x.endsWith('.css'))) {
  const abs = path.join(assetsDir, f)
  if (fs.statSync(abs).mtimeMs < beforeMtime) {
    console.error(`✗ 产物 ${f} 比源码旧：先跑 npm run build，否则结论基于旧产物`)
    process.exit(2)
  }
  for (const r of parseRules(fs.readFileSync(abs, 'utf8'))) {
    const bare = r.selector.replace(/_([0-9a-z]{5})_\d+/g, '')
    // ⚠️ 产物里的类名是 `._quizOption_1a0d4_52`：选择器的点后面紧跟一个**下划线**
    // （`._` 是哈希名的起始符），而源码里是 `.quizOption`。
    // 所以匹配必须允许点与类名之间多一个下划线 —— 否则一条都匹配不到，
    // 而"匹配不到"会被误读成"规则全丢了"（本轮真踩过）。
    if ([...Object.values(RENAME)].some((w) => new RegExp(`\\._?${w}(?![\\w-])`).test(bare))) {
      afterRules.push({ side: f, context: r.context, selector: bare, decls: canonDecls(r.decls) })
    }
  }
}

if (beforeRules.length === 0 || afterRules.length === 0) {
  console.error(
    `✗ 解析结果异常：迁移前 ${beforeRules.length} 条 / 迁移后 ${afterRules.length} 条。` +
      `任何一侧为 0 都说明解析或路径有问题，不能据此下"没丢"的结论。`,
  )
  process.exit(3)
}

const key = (r) => `${r.context} ${neutralSelector(r.selector)}`
const afterByKey = new Map()
for (const r of afterRules) {
  if (!afterByKey.has(key(r))) afterByKey.set(key(r), [])
  afterByKey.get(key(r)).push(r)
}

console.log('════════ 迁移前后规则清单差集 ════════')
console.log(`迁移前（HEAD 源码）：${beforeRules.length} 条`)
console.log(`迁移后（dist 产物）：${afterRules.length} 条\n`)

let exact = 0
let changed = 0
let lost = 0
for (const b of beforeRules) {
  const cands = afterByKey.get(key(b)) || []
  if (cands.length === 0) {
    lost++
    console.log(`✗ 丢失：${key(b)}`)
    console.log(`     原声明：${b.decls.join('; ')}`)
    continue
  }
  const match = cands.find((c) => c.decls.join(';') === b.decls.join(';'))
  if (match) {
    exact++
    console.log(`✓ 逐字保留：${key(b)}`)
  } else {
    changed++
    console.log(`~ 值有变化：${key(b)}`)
    console.log(`     前：${b.decls.join('; ')}`)
    console.log(`     后：${cands[0].decls.join('; ')}`)
  }
}

console.log('\n──── 新增的规则（产物有、迁移前无）────')
const beforeKeys = new Set(beforeRules.map(key))
const added = afterRules.filter((r) => !beforeKeys.has(key(r)))
if (added.length === 0) console.log('（无）')
for (const a of added) console.log(`+ ${key(a)} { ${a.decls.join('; ')} }`)

// ── 动画绑定检查：引用的动画必须与定义在同一个产物文件里 ──
// 这是本轮真正抓到 bug 的地方：`animation: scaleIn` 搬进模块后被哈希成
// `_scaleIn_<hash>`，而该 @keyframes 定义在**另一个**产物文件（index.css）里
// —— 名字对不上，动画静默消失。所以判据不是"文本像不像"，
// 而是"引用的名字在自己这个文件里有没有定义"。
console.log('\n──── 动画绑定检查（引用的动画名必须在同一产物文件内有定义）────')
const NOT_A_NAME =
  /^(none|infinite|linear|ease|ease-in|ease-out|ease-in-out|alternate|alternate-reverse|reverse|forwards|backwards|both|normal|running|paused|initial|inherit|unset|revert|steps)$/
let bindingOk = true
for (const f of fs.readdirSync(assetsDir).filter((x) => x.endsWith('.css'))) {
  const css = fs.readFileSync(path.join(assetsDir, f), 'utf8')
  const defined = new Set([...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]))
  const used = new Set()
  for (const m of css.matchAll(/animation(?:-name)?\s*:\s*([^;{}]+)/g)) {
    for (const group of m[1].split(',')) {
      for (const tok of group.trim().split(/\s+/)) {
        if (!tok || /^[\d.]/.test(tok) || NOT_A_NAME.test(tok)) continue
        if (!/^[A-Za-z_-][\w-]*$/.test(tok)) continue
        used.add(tok)
      }
    }
  }
  const dangling = [...used].filter((u) => !defined.has(u))
  if (dangling.length) {
    bindingOk = false
    console.log(`✗ ${f}: 引用但未定义 → ${dangling.join(', ')}`)
  } else if (used.size) {
    console.log(`✓ ${f}: 引用 ${used.size} 个动画，全部在本文件内有定义（${[...used].join(', ')}）`)
  }
}

// ── 动画体比对：改名不算丢，动画体必须一字不差 ──
console.log('\n──── 动画体比对（模块内改名后的动画，动画体必须与全局原版一致）────')
const { execFileSync } = await import('node:child_process')
const baseCss = execFileSync('git', ['show', 'HEAD:frontend/src/styles/base.css'], { encoding: 'utf8' })
const normKeyframes = (css, name) => {
  const re = new RegExp(`@keyframes\\s+${name}\\s*\\{`, 'i')
  const i = css.search(re)
  if (i < 0) return null
  let depth = 0
  let j = css.indexOf('{', i)
  const start = j
  for (; j < css.length; j++) {
    if (css[j] === '{') depth++
    else if (css[j] === '}') {
      depth--
      if (depth === 0) break
    }
  }
  return css
    .slice(start + 1, j)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s*:\s*/g, ':')
    .replace(/;\s*/g, ';')
    .replace(/\s*,\s*/g, ',')
    .replace(/^;|;$/g, '')
    .trim()
    .toLowerCase()
}
const moduleCss = fs.readFileSync(path.join(root, 'src/components/quiz/QuizAnswerCard.module.css'), 'utf8')
let animOk = true
for (const [orig, renamed] of [
  ['scaleIn', 'feedbackScaleIn'],
  ['shake', 'feedbackShake'],
]) {
  const a = normKeyframes(baseCss, orig)
  const b = normKeyframes(moduleCss, renamed)
  const same = a !== null && a === b
  if (!same) animOk = false
  console.log(`${same ? '✓' : '✗'} @keyframes ${orig} → ${renamed}`)
  if (!same) {
    console.log(`     全局：${a}`)
    console.log(`     模块：${b}`)
  }
}

console.log(
  `\n════════ 汇总：逐字保留 ${exact} / 值有变化 ${changed} / 丢失 ${lost} / ` +
    `动画绑定 ${bindingOk ? '全部自洽' : '有悬空'} / 动画体一致 ${animOk ? '是' : '否'} ════════`,
)

// 动画被**有意**改名并搬进模块，所以"新增 feedbackScaleIn/feedbackShake"不算事故；
// 其余新增都值得看一眼，但不阻断（可能是有意的补充规则）。
process.exit(lost > 0 || !animOk || !bindingOk ? 1 : 0)
