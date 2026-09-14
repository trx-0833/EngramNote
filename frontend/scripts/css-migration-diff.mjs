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
 *   - 归一化：剥哈希后缀、统一值写法、类名映射成中性占位符 `.C0`/`.C1`…；
 *   - 对齐：A 有 B 无 = **丢失**（逐条交代）；B 有 A 无 = **新增**（同样交代）；
 *     两边都有但值不同 = **值有变化**（必须能说清为什么）。
 *
 * ## 从"一份硬编码的试点切片"改成"按批次声明"（5.6 第一批时改的）
 *
 * 试点轮里 RENAME / WANTED_SELECTOR / 读哪两个文件全是写死的常量，
 * 到了第二批就完全用不了。现在每个批次在 `BATCHES` 里声明：
 * 迁移前的全局样式表、这批搬走的**老类名**、以及每个类名的新家。
 * 新类名不是手写的，而是由老类名**机械推导**（kebab → camelCase）——
 * 手写映射表就是一处会写错、且写错了没人发现的地方。
 * 推导结果还会回模块文件里核对一遍（`checkRenames` 自检），
 * 所以"推导规则"和"模块里真实写的类名"不可能悄悄分叉。
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
 * ## 选择器强化（`:global(.btn).authSubmit`）也算"声明过的差异"
 *
 * 第一批发现：**模块的 CSS 在产物里排在全局样式表之前**（Vite 按模块图顺序
 * 产出，而静态引入的组件先于 `main.tsx` 里的样式表被求值）。
 * 于是 `.auth-submit` 与全局 `.btn` 这类"同权重、同在产物里"的竞争会反转胜负。
 * 修法是把全局类写进选择器提高权重，产物里表现为 `.btn._authSubmit_hash`。
 * 差集比对时把这段**声明过的**前缀剥掉（`hardened`），否则会被误报成
 * "一条规则丢失 + 一条规则新增"。
 *
 * 用法：node scripts/css-migration-diff.mjs
 *   前置：npm run build（侧别 B 取自 dist 产物）
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { parseRules, classesOf, findRecentRev, readFromGit } from './lib/css-parse.mjs'

const root = process.cwd()

/**
 * 批次声明。加一批就在这里追加一项，不要改上面的逻辑。
 *
 * - `beforeSheets`：这批规则迁移前住在哪些全局样式表里（从 git 读）；
 * - `rev`：从哪个修订读"迁移前"。默认 `HEAD`，**只对已经提交过的批次**
 *   才需要往前挪一格 —— 试点（quiz 切片）已随 `6acb21c` 提交，
 *   所以它的"迁移前"是 `HEAD~1`；第一批尚未提交，用默认的 `HEAD`。
 *   读错修订的表现是"迁移前 0 条"，脚本会直接报错退出，不会静默放过；
 * - `groups`：老类名 → 新家的模块文件（新类名由 kebab→camel 推导后回文件核对）；
 * - `hardened`：为压过全局类而**显式加进选择器**的全局类前缀（老类名 → 前缀）。
 *   第一批曾用过一次（`:global(.btn).authSubmit`），后来改成修正 `main.tsx`
 *   的导入顺序、把选择器还原成单类，所以现在是空的。机制留着：哪天再出现
 *   "必须靠提权才能赢"的正当场景，在这里登记即可 —— 不登记的话差集会把它
 *   误报成"丢失 1 条 + 新增 1 条"；
 * - `keyframes`：搬进模块并改名的 `@keyframes`（动画体必须与全局原版一致）。
 *
 * ⚠️ `rev` 一般**不用写**：留空时脚本按内容自动定位"迁移前"（从 HEAD 往回找
 * 第一个还含有本批老类名的修订）。写死 `HEAD~N` 会被并行的无关提交打乱 ——
 * 第二批期间另一个 agent 提交了一个后端改动，所有相对计数就集体错位了。
 * 这个字段留着只是为了在自动定位不适用时手工兜底。
 */
const BATCHES = [
  {
    id: '试点：quiz 答题切片',
    beforeSheets: ['src/styles/learning.css', 'src/styles/responsive.css'],
    groups: [
      {
        module: 'src/components/quiz/QuizAnswerCard.module.css',
        classes: ['quiz-option', 'quiz-option-selected', 'feedback-correct', 'feedback-incorrect'],
      },
      {
        module: 'src/components/quiz/SelfRatingButtons.module.css',
        classes: ['self-rating-btn'],
      },
    ],
    hardened: {},
    keyframes: [
      {
        fromSheet: 'src/styles/base.css',
        orig: 'scaleIn',
        module: 'src/components/quiz/QuizAnswerCard.module.css',
        renamed: 'feedbackScaleIn',
      },
      {
        fromSheet: 'src/styles/base.css',
        orig: 'shake',
        module: 'src/components/quiz/QuizAnswerCard.module.css',
        renamed: 'feedbackShake',
      },
    ],
  },
  {
    id: '第一批：auth → cleaning → diff → dashboard',
    beforeSheets: [
      'src/styles/auth.css',
      'src/styles/cleaning.css',
      'src/styles/diff.css',
      'src/styles/dashboard.css',
      'src/styles/responsive.css',
    ],
    groups: [
      {
        module: 'src/pages/Auth.module.css',
        classes: [
          'auth-bg',
          'auth-card',
          'auth-title',
          'auth-input-group',
          'auth-input-icon',
          'auth-submit',
          'auth-footer',
        ],
      },
      {
        module: 'src/components/CleaningPanel.module.css',
        classes: [
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
        ],
      },
      {
        module: 'src/components/DiffView.module.css',
        classes: [
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
        ],
      },
      {
        module: 'src/pages/Dashboard.module.css',
        classes: ['dashboard-two-col', 'dashboard-review-card', 'trend-bar', 'trend-bar-warning'],
      },
    ],
    // 曾经是 `{ 'auth-submit': '.btn' }`（`:global(.btn).authSubmit`）。
    // 已改为修正 `main.tsx` 的导入顺序 + 还原单类选择器，
    // 所以这里刻意留空：留空意味着"这一批没有任何选择器文本变化"，
    // 任何新的提权都必须显式登记，否则差集会报"丢失 + 新增"。
    hardened: {},
    keyframes: [
      {
        fromSheet: 'src/styles/base.css',
        orig: 'scaleIn',
        module: 'src/pages/Auth.module.css',
        renamed: 'authScaleIn',
      },
      {
        fromSheet: 'src/styles/base.css',
        orig: 'cleaning-pulse',
        module: 'src/components/CleaningPanel.module.css',
        renamed: 'cleaningPulse',
      },
    ],
  },
  {
    id: '第二批：markdown-extras（ask-ai + selection-menu）',
    // 本批尚未提交 → 迁移前就读 HEAD（= 第一批提交后的状态）
    beforeSheets: ['src/styles/markdown-extras.css'],
    groups: [
      {
        module: 'src/components/NoteAskPanel.module.css',
        classes: [
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
        ],
      },
      {
        module: 'src/pages/notedetail/SelectionMenu.module.css',
        classes: ['selection-menu'],
      },
    ],
    // 本批没有需要提权的规则：`.ask-ai-input` / `.selection-menu` 都是单类选择器，
    // 相对全局层只有元素选择器（`input, textarea` / `button`）能碰上，
    // 权重 (0,1,0) > (0,0,1)，与先后无关。
    hardened: {},
    // 本批不搬 `@keyframes`：同表的 `citation-flash` 属于留在全局的
    // `.markdown-body mark.citation-highlight`，引用与定义仍在同一层。
    keyframes: [],
  },
]

// ── 归一化 ──
/**
 * 值归一化：吃掉压缩器/构建器的等价改写，只留语义。
 *
 * 每一条都要有据可查 —— 下面的替换全部是"同一个颜色的两种写法"或
 * "同一个伪元素的两种写法"，不是把差异抹平：
 *
 * 1. `rgba(15, 52, 96, .08)` ↔ `var(--color-primary-light)`
 *    —— 试点轮的令牌化替换，base.css 里两者数值完全相同，
 *    展开回字面值再比，避免把"用了令牌"误报成"值变了"。
 * 2. `rgba(255,255,255,.92)` ↔ `#ffffffeb`
 *    —— 压缩器把带 alpha 的颜色压成 8 位十六进制（alpha 四舍五入到 8 位：
 *    `.92 × 255 = 234.6 → 235 = 0xeb`）。两侧都归一到 `#rrggbbaa`。
 * 3. `#fffc` ↔ `#ffffffcc` —— 压缩器把可缩写的形式写到最短；同样展开。
 * 4. `color: white` ↔ `color: #fff` —— 命名色被压成十六进制。
 * 5. `content: ''` ↔ `content: ""` —— 引号风格。
 *
 * 这五条都是**产物**层面的等价改写，与"搬家有没有改变语义"无关；
 * 不归一化的话它们会伪装成 6 条"值有变化"，把真正的变化淹掉。
 */
const TOKEN_EQUIV = {
  'var(--color-primary-light)': 'rgba(15,52,96,.08)',
}

/** 8 位十六进制化：`#abc` / `#abcc` / `#aabbcc` / `#aabbccdd` 一律补成 `#aabbccdd` */
function expandHex(s) {
  return s.replace(/#([0-9a-f]{3,8})(?![\w-])/gi, (m, h) => {
    let x = h.toLowerCase()
    // 3 位 / 4 位是缩写形式（4 位那档带 alpha，如 `#fffc` = `#ffffffcc`）
    if (x.length === 3 || x.length === 4) x = x.split('').map((c) => c + c).join('')
    if (x.length === 6) x += 'ff'
    return x.length === 8 ? `#${x}` : m
  })
}

/** `rgba(r,g,b,a)` → `#rrggbbaa`（alpha 四舍五入到 8 位，与压缩器一致） */
function rgbaToHex8(s) {
  return s.replace(
    /rgba\((\d{1,3}),(\d{1,3}),(\d{1,3}),(1|0|\.\d+|0\.\d+)\)/g,
    (m, r, g, b, a) => {
      const av = a === '1' ? 255 : a === '0' ? 0 : Math.round(Number.parseFloat(a) * 255)
      const hex = (n) => Number(n).toString(16).padStart(2, '0')
      return `#${hex(r)}${hex(g)}${hex(b)}${hex(av)}`
    },
  )
}

function canonValue(v) {
  let s = v
    .replace(/\s+/g, ' ')
    .replace(/(?<![\d.])0\.(\d)/g, '.$1') // 0.3s → .3s
    .replace(/translateX\(([^)]*)\)/g, 'translate($1)')
    .replace(/translateY\(([^)]*)\)/g, 'translate(0, $1)')
    .replace(/\s*,\s*/g, ',')
    .replace(/"/g, "'") // content: "" → content: ''
    .replace(/\bwhite\b/g, '#ffffff') // 命名色 → 十六进制
    .replace(/\s+/g, '')
    .toLowerCase()
  for (const [token, literal] of Object.entries(TOKEN_EQUIV)) {
    s = s.split(token).join(literal)
  }
  return expandHex(rgbaToHex8(s))
}

/**
 * 声明列表 → "prop:val" 排序后的数组。
 *
 * `animation` / `animation-name` 的值**整条归一成 `NAME`**，不参与文本比对：
 * 动画名在产物里被哈希（`scaleIn` → `_feedbackScaleIn_1a0d4_1`），
 * 逐字比文本必然不等。而动画真正要验的两件事 ——
 * "引用的动画在产物里有没有定义"（绑定）与"动画体是否与全局原版一致"——
 * 由文件末尾两个独立检查负责，比硬凑成一次字符串比对可靠得多。
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
 * 剥掉 CSS Modules 的哈希后缀：`_quizOption_1a0d4_52` → `_quizOption`。
 * 哈希形如 `_<5位>_<行号>`，但**只剥这一种**，不要用宽泛的
 * `_[0-9a-z]+_\d+` —— 那会把 `_feedbackscalein_1a0d4_1` 里经过
 * 别名替换后的名字也啃掉一截，剩下一个孤零零的下划线，
 * 让两侧看起来"值不同"（试点轮踩过，两条 animation 报了假警报）。
 */
const stripHash = (s) => s.replace(/_([0-9a-z]{5})_\d+(?![0-9a-z])/g, '')

/** `auth-input-group` → `authInputGroup`（本项目模块类名的唯一推导规则） */
const kebabToCamel = (name) => name.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())

/**
 * 定位"迁移前"的修订：从 HEAD 往回找第一个**还含有本批老类名**的修订。
 *
 * 不再用 `HEAD~N` 数格子：并行 agent 提交无关改动会让所有相对计数集体错位
 * （第二批真的遇到了）。按内容定位与提交顺序无关，见
 * `lib/css-parse.mjs` 的 `findRecentRev`。
 */
function resolveBeforeRev(batch, oldNames) {
  return findRecentRev((rev) =>
    batch.beforeSheets.some((rel) => {
      const abs = path.join(root, rel)
      const css = readFromGit(abs, rev)
      return parseRules(css).some((r) => classesOf(r.selector).some((c) => oldNames.has(c)))
    }),
  )
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * 自检：推导出来的新类名必须真的写在它声称的模块文件里。
 * 没有这一步，"机械推导"就可能与模块里实际写的类名分叉
 * （比如有人手写了 `authForm` 而不是 `authCard`），
 * 而分叉的表现是"规则全丢"这种吓人的假警报。
 */
function checkRenames(batch) {
  const problems = []
  for (const g of batch.groups) {
    const abs = path.join(root, g.module)
    if (!fs.existsSync(abs)) {
      problems.push(`${g.module} 不存在`)
      continue
    }
    const css = fs.readFileSync(abs, 'utf8')
    for (const old of g.classes) {
      const camel = kebabToCamel(old)
      if (!new RegExp(`\\.${camel}(?![\\w-])`).test(css)) {
        problems.push(`${g.module} 里找不到 .${camel}（由 .${old} 推导而来）`)
      }
    }
  }
  return problems
}

/** 批次 → 归一化选择器所需的映射表（老名 → 新名 / 占位符） */
function batchMaps(batch) {
  const remap = [] // [oldName, newName, placeholder]
  const all = batch.groups.flatMap((g) => g.classes)
  // 占位符按名字排序分配，两批之间互不干扰、批内稳定
  const sorted = [...all].sort()
  const placeholderOf = new Map(sorted.map((n, i) => [n, `.C${i}`]))
  for (const g of batch.groups) {
    for (const old of g.classes) {
      remap.push([old, kebabToCamel(old), placeholderOf.get(old)])
    }
  }
  // ⚠️ 替换顺序必须"长名优先"，否则 `.diffLine` 会先命中
  // `.diffLinePrefix` 的前缀（不过 `(?![\w-])` 边界已经挡住了这种情形，
  // 两道保险都留着）。
  remap.sort((a, b) => b[1].length - a[1].length)
  return remap
}

/**
 * 选择器 → 中性占位符。
 *
 * ⚠️ 每个类名必须映射到**不同**的占位符（`.C0` / `.C1` …），
 * 不能都换成同一个 `.X`：那样 `.quiz-option` / `.quiz-option-selected` /
 * `.feedback-correct` 会挤到同一个 key 上，查表时拿到的是别人那条规则，
 * 于是报出"值有变化"的假警报（试点轮踩过：5 条规则被并成 1 个 key）。
 *
 * 另注意产物里是 `._quizOption_hash`：点后面多一个下划线，
 * 所以 `\.` 后面要允许 `_?`，否则一条都对不上。
 */
function neutralSelector(sel, remap) {
  let s = stripHash(sel)
  for (const [oldName, newName, ph] of remap) {
    s = s.replace(new RegExp(`\\._?${escapeRe(newName)}(?![\\w-])`, 'g'), ph)
    s = s.replace(new RegExp(`\\._?${escapeRe(oldName)}(?![\\w-])`, 'g'), ph)
  }
  // 压缩器把 `::before` / `::after` 压成 `:before` / `:after`（同义写法）。
  // 不归一化的话，四条带伪元素的规则会被报成"丢失 + 新增"。
  return s.replace(/::/g, ':').replace(/\s+/g, ' ').trim()
}

// ── 产物新鲜度（拿旧产物下结论是这类脚本最危险的失败方式）──
const assetsDir = path.join(root, 'dist/assets')
if (!fs.existsSync(assetsDir)) {
  console.error('✗ 没有 dist/assets：先跑 npm run build')
  process.exit(2)
}
const distSheets = fs.readdirSync(assetsDir).filter((x) => x.endsWith('.css'))
const distMtime = Math.max(...distSheets.map((f) => fs.statSync(path.join(assetsDir, f)).mtimeMs))
const newestSrc = Math.max(
  ...fs
    .readdirSync(path.join(root, 'src/styles'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => fs.statSync(path.join(root, 'src/styles', f)).mtimeMs),
)
if (newestSrc > distMtime) {
  console.error('✗ 产物比源码旧：先跑 npm run build，否则结论基于旧产物')
  process.exit(2)
}

let failed = false

for (const batch of BATCHES) {
  console.log(`\n════════ 迁移前后规则清单差集：${batch.id} ════════`)

  const renameProblems = checkRenames(batch)
  if (renameProblems.length) {
    console.error('✗ 类名推导自检失败（先修这个，否则下面全是假警报）：')
    for (const p of renameProblems) console.error(`   - ${p}`)
    process.exit(3)
  }

  const remap = batchMaps(batch)
  const oldNames = new Set(remap.map((r) => r[0]))
  const newNames = [...new Set(remap.map((r) => r[1]))]
  const key = (r) => `${r.context} ${neutralSelector(r.selector, remap)}`

  // ── 侧别 A：迁移前（git 修订里的源码） ──
  const beforeRules = []
  const rev = batch.rev || resolveBeforeRev(batch, oldNames)
  if (!rev) {
    console.error(
      `✗ ${batch.id}：从 HEAD 往回 40 个提交里找不到"还含有本批类名"的修订。` +
        `要么类名写错了，要么这个批次其实没迁移过 —— 两种情况都不该继续编差集。`,
    )
    process.exit(3)
  }
  for (const rel of batch.beforeSheets) {
    const abs = path.join(root, rel)
    // 侧别 A 用 git 读，这样"迁移前"是那个修订的真实内容，
    // 而不是某个人手工留存、可能已经过期的快照文件
    const repoRel = path.relative(path.join(root, '..'), abs).replace(/\\/g, '/')
    const css = execFileSync('git', ['show', `${rev}:${repoRel}`], { encoding: 'utf8' })
    for (const r of parseRules(css)) {
      if (classesOf(r.selector).some((c) => oldNames.has(c))) {
        beforeRules.push({
          side: `${rel}@${rev}`,
          context: r.context,
          selector: r.selector,
          decls: canonDecls(r.decls),
        })
      }
    }
  }

  // ── 侧别 B：迁移后（dist 产物） ──
  const afterRules = []
  for (const f of distSheets) {
    const css = fs.readFileSync(path.join(assetsDir, f), 'utf8')
    for (const r of parseRules(css)) {
      const bare = stripHash(r.selector)
      // 先按"声明的选择器强化"把前缀摘掉，再判定这条规则属不属于本批
      let probe = bare
      for (const [old, prefix] of Object.entries(batch.hardened || {})) {
        const camel = kebabToCamel(old)
        probe = probe.replace(
          new RegExp(`^${escapeRe(prefix)}\\._?${escapeRe(camel)}(?![\\w-])`),
          `.${camel}`,
        )
      }
      if (!newNames.some((w) => new RegExp(`\\._?${w}(?![\\w-])`).test(probe))) continue
      afterRules.push({ side: f, context: r.context, selector: probe, decls: canonDecls(r.decls) })
    }
  }

  if (beforeRules.length === 0 || afterRules.length === 0) {
    console.error(
      `✗ 解析结果异常：迁移前 ${beforeRules.length} 条 / 迁移后 ${afterRules.length} 条。` +
        `任何一侧为 0 都说明解析或路径有问题，不能据此下"没丢"的结论。`,
    )
    process.exit(3)
  }

  const afterByKey = new Map()
  for (const r of afterRules) {
    if (!afterByKey.has(key(r))) afterByKey.set(key(r), [])
    afterByKey.get(key(r)).push(r)
  }

  console.log(`迁移前（git ${rev} 源码）：${beforeRules.length} 条`)
  console.log(`迁移后（dist 产物）：${afterRules.length} 条`)
  console.log(`迁移前来源：${[...new Set(beforeRules.map((r) => r.side))].join(', ')}`)
  console.log(`迁移后来源：${[...new Set(afterRules.map((r) => r.side))].join(', ')}\n`)

  let exact = 0
  let changed = 0
  let lost = 0
  /** 产物"多出来的"声明：只可能是构建器注入（如 esbuild 补 `-webkit-user-select`），
      属于加法而非丢失。单独统计、逐条列出，但不当作失败 —— 否则真变化会被噪音淹掉。 */
  const extraDecls = []

  /** "prop:val" 数组 → Map(prop → val) */
  const toMap = (arr) => {
    const m = new Map()
    for (const d of arr) {
      const i = d.indexOf(':')
      m.set(d.slice(0, i), d.slice(i + 1))
    }
    return m
  }

  for (const b of beforeRules) {
    const cands = afterByKey.get(key(b)) || []
    if (cands.length === 0) {
      lost++
      console.log(`✗ 丢失：${key(b)}`)
      console.log(`     原声明：${b.decls.join('; ')}`)
      continue
    }
    const bm = toMap(b.decls)
    let best = null
    for (const c of cands) {
      const cm = toMap(c.decls)
      const missing = [...bm.keys()].filter((k) => !cm.has(k))
      const diff = [...bm.keys()].filter((k) => cm.has(k) && cm.get(k) !== bm.get(k))
      if (missing.length === 0 && diff.length === 0) {
        best = { c, extra: [...cm.keys()].filter((k) => !bm.has(k)).map((k) => `${k}:${cm.get(k)}`) }
        break
      }
      if (!best) best = { c, missing, diff, extra: [] }
    }
    if (!best || best.missing?.length || best.diff?.length) {
      lost++
      console.log(`✗ 丢失：${key(b)}`)
      console.log(`     原声明：${b.decls.join('; ')}`)
      if (best?.missing?.length) console.log(`     产物缺：${best.missing.join(', ')}`)
      continue
    }
    if (best.extra.length) extraDecls.push({ key: key(b), extra: best.extra })
    exact++
    console.log(`✓ 逐字保留：${key(b)}`)
    if (best.extra.length) console.log(`     ※ 产物多出：${best.extra.join('; ')}（构建器注入）`)
  }

  console.log('\n──── 新增的规则（产物有、迁移前无）────')
  const beforeKeys = new Set(beforeRules.map(key))
  const added = afterRules.filter((r) => !beforeKeys.has(key(r)))
  if (added.length === 0) console.log('（无）')
  for (const a of added) console.log(`+ ${key(a)} { ${a.decls.join('; ')} }`)

  if (extraDecls.length) {
    console.log('\n──── 产物多出的声明（加法，不是丢失；来源是构建器而非本次改动）────')
    console.log(`   共 ${extraDecls.length} 条规则出现多余声明：`)
    for (const e of extraDecls) console.log(`   ${e.key} → ${e.extra.join('; ')}`)
  }

  // 选择器强化是"声明过的差异"，单独说清楚：它改了选择器文本，
  // 但按定义**不改变任何属性的取值**（权重只升不降）。
  const hardenedUsed = Object.entries(batch.hardened || {})
  if (hardenedUsed.length) {
    console.log('\n──── 声明过的选择器强化（权重提高，属性值不变）────')
    for (const [old, prefix] of hardenedUsed) {
      const camel = kebabToCamel(old)
      const hits = afterRules.filter((r) =>
        new RegExp(`\\._?${escapeRe(camel)}(?![\\w-])`).test(r.selector),
      ).length
      console.log(
        `   ${old} → 模块里写作 \`:global(${prefix}).${camel}\`，产物里是 \`${prefix}.${camel}\`（匹配 ${hits} 条）`,
      )
    }
  }

  console.log(
    `\n批次汇总：逐字保留 ${exact} / 值有变化 ${changed} / 丢失 ${lost}`,
  )
  if (lost > 0) failed = true
}

// ── 动画绑定检查：引用的动画必须与定义在同一个产物文件里 ──
// 这是试点轮真正抓到 bug 的地方：`animation: scaleIn` 搬进模块后被哈希成
// `_scaleIn_<hash>`，而该 @keyframes 定义在**另一个**产物文件（index.css）里
// —— 名字对不上，动画静默消失。所以判据不是"文本像不像"，
// 而是"引用的名字在自己这个文件里有没有定义"。
console.log('\n──── 动画绑定检查（引用的动画名必须在同一产物文件内有定义）────')
const NOT_A_NAME =
  /^(none|infinite|linear|ease|ease-in|ease-out|ease-in-out|alternate|alternate-reverse|reverse|forwards|backwards|both|normal|running|paused|initial|inherit|unset|revert|steps)$/
let bindingOk = true
for (const f of distSheets) {
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
const sheetCache = new Map()
const readHeadSheet = (rel) => {
  if (!sheetCache.has(rel)) {
    sheetCache.set(rel, execFileSync('git', ['show', `HEAD:frontend/${rel}`], { encoding: 'utf8' }))
  }
  return sheetCache.get(rel)
}
const normKeyframes = (css, name) => {
  const re = new RegExp(`@keyframes\\s+${escapeRe(name)}\\s*\\{`, 'i')
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
let animOk = true
for (const batch of BATCHES) {
  for (const kf of batch.keyframes) {
    const a = normKeyframes(readHeadSheet(kf.fromSheet), kf.orig)
    const b = normKeyframes(fs.readFileSync(path.join(root, kf.module), 'utf8'), kf.renamed)
    const same = a !== null && a === b
    if (!same) animOk = false
    console.log(`${same ? '✓' : '✗'} @keyframes ${kf.orig}（${kf.fromSheet}）→ ${kf.renamed}（${kf.module}）`)
    if (!same) {
      console.log(`     全局：${a}`)
      console.log(`     模块：${b}`)
    }
  }
}

console.log(
  `\n════════ 汇总：丢失 ${failed ? '有' : '0'} / ` +
    `动画绑定 ${bindingOk ? '全部自洽' : '有悬空'} / 动画体一致 ${animOk ? '是' : '否'} ════════`,
)

// 动画被**有意**改名并搬进模块，所以"新增 feedbackScaleIn/cleaningPulse…"不算事故；
// 其余新增都值得看一眼，但不阻断（可能是有意的补充规则）。
process.exit(failed || !animOk || !bindingOk ? 1 : 0)
