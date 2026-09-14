/**
 * 生成 5.6 迁移证据文件（overhaul-plan 5.6）
 *
 * ## 为什么由 Node 写文件，而不是在 shell 里重定向
 *
 * 本项目已经两次被 PowerShell 写坏 UTF-8，而证据文件里全是中文注释。
 * 实测本机 `Out-File -Encoding utf8` 会加 BOM（EF BB BF），
 * `*> 重定向更会写成 UTF-16LE（FF FE）—— 两种都不是本项目要的编码。
 * `fs.writeFileSync(..., 'utf8')` 写出来是无 BOM 的 UTF-8，且只有一个实现。
 *
 * ## 生成什么
 *
 *   docs/migration-evidence/5.6-01-before-*.md              试点：迁移前规则清单
 *   docs/migration-evidence/5.6-02-rule-diff.md             迁移前后差集（**含所有批次**）
 *   docs/migration-evidence/5.6-03-built-css.md             产物 CSS 校验（**含所有批次**）
 *   docs/migration-evidence/5.6-04-before-batch1.md         第一批：迁移前规则清单
 *   docs/migration-evidence/5.6-05-before-batch2.md         第二批：迁移前规则清单
 *   docs/migration-evidence/5.6-06-before-batch3.md         第三批：迁移前规则清单
 *   docs/migration-evidence/5.6-07-markdown-adhd-stayed-global.md
 *                                                           第三批的"停"：`.adhd-*` 留全局
 *   docs/migration-evidence/5.6-08-before-batch4.md          第四批（序 5）迁移前规则清单
 *   docs/migration-evidence/5.6-09-conflict-resolution.md    第四批的冲突裁决：16 条属性的实测胜者
 *
 * ⚠️ 修订（rev）**不用手写**：两个脚本都从 HEAD 往回按内容找"还含有这批老类名"
 * 的第一个提交。原来写死 `HEAD~1` / `HEAD~2`，被并行的无关提交打乱过 ——
 * 第二批期间另一个 agent 提交了一个后端改动，相对计数就集体错位，
 * 三个批次的"迁移前"全指错。给错的表现是"清单 0 条"，脚本会报错退出
 * 而不是写出一份空证据。
 *
 * 用法：node scripts/gen-migration-evidence.mjs
 *   前置：已跑过 npm run build（差集与产物校验都取自 dist 产物）
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { parseRules, splitSelectors, findRecentRev, readFromGit } from './lib/css-parse.mjs'

const root = process.cwd()
const outDir = path.join(root, 'docs/migration-evidence')
fs.mkdirSync(outDir, { recursive: true })

function run(script, args = []) {
  try {
    return {
      code: 0,
      out: execFileSync('node', [path.join(root, 'scripts', script), ...args], {
        cwd: root,
        encoding: 'utf8',
        maxBuffer: 32 * 1024 * 1024,
      }),
    }
  } catch (e) {
    // 退出码非 0 也要把输出留下来：失败信息本身就是证据
    return { code: e.status ?? 1, out: (e.stdout || '') + (e.stderr || '') }
  }
}

const files = []

function write(name, title, body) {
  const text = `<!-- 由 scripts/gen-migration-evidence.mjs 生成，请勿手改 -->\n\n# ${title}\n\n${body}\n`
  // 断言无 BOM：这是本项目踩过两次的坑，留一道自检
  const buf = Buffer.from(text, 'utf8')
  if (buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf) {
    throw new Error(`${name} 写出来带了 BOM`)
  }
  fs.writeFileSync(path.join(outDir, name), text, 'utf8')
  files.push({ name, bytes: buf.length })
}

/** 跑一次清单并把结果写成一个证据文件（修订按内容自动定位，不写死） */
function inventoryEvidence({ name, title, sheet, classes }) {
  const r = run('css-rule-inventory.mjs', [sheet, '--class', classes.join(','), '--from-git'])
  if (r.code !== 0) throw new Error(`清单生成失败（${sheet}）：${r.out}`)
  write(name, title, `取自 \`git show <rev>:frontend/${sheet}\`，修订按内容自动定位。\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}

// ── 1. 试点（quiz 切片）迁移前清单 ──
inventoryEvidence({
  name: '5.6-01-before-learning-css.md',
  title: '5.6 迁移前：learning.css 中属于答题卡片切片的规则',
  sheet: 'src/styles/learning.css',
  classes: ['quiz-option', 'quiz-option-selected', 'feedback-correct', 'feedback-incorrect'],
})
inventoryEvidence({
  name: '5.6-01-before-responsive-css.md',
  title: '5.6 迁移前：responsive.css 中 .self-rating-btn 的规则',
  sheet: 'src/styles/responsive.css',
  classes: ['self-rating-btn'],
})

// ── 2. 第一批（auth → cleaning → diff → dashboard）迁移前清单：尚未提交，故从 HEAD 取 ──
const batch1Sheets = [
  {
    sheet: 'src/styles/auth.css',
    classes: [
      'auth-bg', 'auth-card', 'auth-title', 'auth-input-group',
      'auth-input-icon', 'auth-submit', 'auth-footer',
    ],
  },
  {
    sheet: 'src/styles/cleaning.css',
    classes: [
      'cleaning-panel', 'cleaning-stats', 'cleaning-progress', 'cleaning-progress-bar',
      'duplicate-blocks', 'duplicate-block', 'duplicate-block-header', 'duplicate-block-info',
      'duplicate-block-index', 'duplicate-block-similarity', 'duplicate-block-actions',
      'duplicate-block-compare', 'duplicate-block-text-label', 'duplicate-block-text-content',
      'duplicate-block-text-empty',
    ],
  },
  {
    sheet: 'src/styles/diff.css',
    classes: [
      'diff-container', 'diff-summary', 'diff-header', 'diff-col-label', 'diff-body',
      'diff-block', 'diff-line', 'diff-line-prefix', 'diff-line-number', 'diff-line-content',
      'diff-line-removed', 'diff-line-added', 'diff-line-unchanged',
    ],
  },
  {
    sheet: 'src/styles/dashboard.css',
    classes: ['dashboard-two-col', 'dashboard-review-card', 'trend-bar', 'trend-bar-warning'],
  },
  {
    sheet: 'src/styles/responsive.css',
    classes: ['dashboard-two-col', 'dashboard-review-card'],
  },
]
const parts = []
for (const s of batch1Sheets) {
  // 不给 --rev：清单脚本会**按内容**自动定位"迁移前"（从 HEAD 往回找第一个
  // 还含有这些类名的修订）。写死 HEAD~N 会被并行的无关提交打乱 —— 第二批
  // 期间另一个 agent 提交了一个后端改动，相对计数就集体错位了。
  const r = run('css-rule-inventory.mjs', [s.sheet, '--class', s.classes.join(','), '--from-git'])
  if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
  parts.push(`## ${s.sheet}\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}
write(
  '5.6-04-before-batch1.md',
  '5.6 迁移前：第一批（auth / cleaning / diff / dashboard 私有部分）的规则清单',
  '五个样式表各自只列出**本批搬走的那些类名**对应的规则（留在全局的\n' +
    '`.stat-card*` / `.stat-label` / `.progress-bar*` 不在清单里，它们没有搬）。\n' +
    '修订由 `css-rule-inventory.mjs` **按内容自动定位**（第一个还含有这些类名的\n' +
    '提交），所以清单头部的 `@HEAD~N` 会随提交数变化，规则内容不会。\n\n' +
    parts.join('\n\n'),
)

// ── 3. 第二批（markdown-extras）迁移前清单 ──
const batch2Sheets = [
  {
    sheet: 'src/styles/markdown-extras.css',
    classes: [
      'ask-ai-panel', 'ask-ai-header', 'ask-ai-title', 'ask-ai-close', 'ask-ai-body',
      'ask-ai-input', 'ask-ai-selected-hint', 'ask-ai-actions', 'ask-ai-question',
      'ask-ai-thinking', 'ask-ai-answer', 'ask-ai-error', 'ask-ai-provider',
      'selection-menu',
    ],
  },
]
const parts2 = []
for (const s of batch2Sheets) {
  const r = run('css-rule-inventory.mjs', [s.sheet, '--class', s.classes.join(','), '--from-git'])
  if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
  parts2.push(`## ${s.sheet} @HEAD\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}
write(
  '5.6-05-before-batch2.md',
  '5.6 迁移前：第二批（markdown-extras 的 `.ask-ai-*` 与 `.selection-menu`）的规则清单',
  '只列出**本批搬走**的类名。同表里留在全局的 KaTeX（`.markdown-body .katex*`、\n' +
    '`.katex-error`）与批注高亮（`.markdown-body .annotation-*`、\n' +
    '`.markdown-body mark.citation-highlight` + `@keyframes citation-flash`）\n' +
    '不在清单里：它们作用在 `marked` / KaTeX 生成的 HTML 字符串上，没有组件可挂类名。\n' +
    '取自 `git show HEAD:frontend/...`（第二批尚未提交）。\n\n' +
    parts2.join('\n\n'),
)

// ── 4. 第三批（learning 按归属拆分 + dashboard 余下）迁移前清单 ──
const batch3Sheets = [
  {
    // 按"谁在用"拆成三组：`.qa-*` → QA 页、`.upload-zone*` → Upload 页、
    // `.search-input-*` → NotesList 页。留在全局的（`.filter-pill*` /
    // `.segment-*` / `.collapse-arrow*` / `.state-*` / `.spinner`）不在清单里。
    sheet: 'src/styles/learning.css',
    classes: [
      'qa-user-bubble', 'qa-ai-card',
      'upload-zone', 'upload-zone-active',
      'search-input-wrapper', 'search-input-icon',
    ],
  },
  {
    // `.list-toolbar` 这个类名**只定义在补丁层**（全项目唯一一处），
    // `.stat-number` 是被搬走的统计卡片类的窄屏档 —— 三条都必须跟着组件走，
    // 否则类名哈希后它们永远选不中任何东西（雷区 2）。
    sheet: 'src/styles/responsive.css',
    classes: ['list-toolbar', 'search-input-wrapper', 'stat-number'],
  },
  {
    // dashboard.css 余下的 9 条：先抽 `components/StatCard.tsx`，样式随组件进模块
    sheet: 'src/styles/dashboard.css',
    classes: [
      'stat-card', 'stat-card-blue', 'stat-card-green', 'stat-card-gold',
      'stat-card-purple', 'stat-number', 'stat-label',
    ],
  },
]
const parts3 = []
for (const s of batch3Sheets) {
  const r = run('css-rule-inventory.mjs', [s.sheet, '--class', s.classes.join(','), '--from-git'])
  if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
  parts3.push(`## ${s.sheet}\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}
write(
  '5.6-06-before-batch3.md',
  '5.6 迁移前：第三批（learning 按归属拆分 + dashboard 余下 → StatCard）的规则清单',
  '三个样式表各自只列出**本批搬走**的类名对应的规则。修订由\n' +
    '`css-rule-inventory.mjs` **按内容自动定位**（从 HEAD 往回找第一个还含有\n' +
    '这些类名的提交），所以清单头部的 `@HEAD~N` 会随提交数变化，规则内容不会。\n\n' +
    '`dashboard.css` 的 `.progress-bar*`（3 条）与 `learning.css` 的\n' +
    '`.filter-pill*` / `.segment-*` / `.collapse-arrow*` / `.state-*` / `.spinner`\n' +
    '**留全局**，不在清单里 —— 判据（grep 出的文件数）写在两个样式表的文件头。\n\n' +
    parts3.join('\n\n'),
)

// ── 6. 第四批（序 5）：assessment.css 按归属拆分 + 冲突裁决 ──
const batch4Sheets = [
  {
    // 本页私有的 20 条：`.score-bar*`(9) / `.quiz-question-*`(4) /
    // `.score-summary-*`(3) / `.knowledge-points-*`(4)。
    // 留全局的 `.assessment-header` / `-title` / `-subtitle` 与
    // `.note-select-card*` 不在清单里（学习评估页 + 项目页共用，规范 §4 第 2 条）。
    sheet: 'src/styles/assessment.css',
    classes: [
      'score-bar', 'score-bar-header', 'score-bar-label', 'score-bar-value',
      'score-bar-track', 'score-bar-fill', 'score-bar-fill-high',
      'score-bar-fill-mid', 'score-bar-fill-low',
      'quiz-question-card', 'quiz-question-card-active', 'quiz-question-number',
      'quiz-question-text',
      'score-summary-card', 'score-summary-number', 'score-summary-label', 'score-value',
      'knowledge-points-grid', 'knowledge-points-section',
    ],
  },
  {
    // 补丁层里属于学习评估页的 14 条 —— 这 14 条**全部是那场冲突的胜者**
    // （权重相同、本文件在 `main.tsx` 里更靠后），逐字进模块。
    sheet: 'src/styles/refinements.css',
    classes: [
      'quiz-question-card', 'quiz-question-card-active', 'quiz-question-number',
      'quiz-question-text',
      'score-summary-card', 'score-summary-number', 'score-summary-label', 'score-value',
      'score-bar-value',
    ],
  },
  {
    // 480px 的 `.knowledge-points-grid`：留在补丁层的话，类名进模块后
    // 这个选择器永远选不中任何东西（规范 §3 雷区 2）。
    sheet: 'src/styles/responsive.css',
    classes: ['knowledge-points-grid'],
  },
]
const parts4 = []
for (const s of batch4Sheets) {
  const r = run('css-rule-inventory.mjs', [s.sheet, '--class', s.classes.join(','), '--from-git'])
  if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
  parts4.push(`## ${s.sheet}\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}
write(
  '5.6-08-before-batch4.md',
  '5.6 迁移前：第四批（序 5 assessment.css 按归属拆分 + refinements 的冲突胜者）的规则清单',
  '三个样式表各自只列出**本批搬走**的类名对应的规则：`assessment.css` 的 20 条、\n' +
    '`refinements.css` 的 14 条（这 14 条就是那场冲突的**胜者**）、\n' +
    '`responsive.css` 的 1 条 480px 规则。修订由 `css-rule-inventory.mjs`\n' +
    '**按内容自动定位**（从 HEAD 往回找第一个还含有这些类名的提交），\n' +
    '所以清单头部的 `@HEAD~N` 会随提交数变化，规则内容不会。\n\n' +
    '留全局的 `.assessment-header` / `-title` / `-subtitle`（+ 项目页 `ProjectsHeader.tsx`）\n' +
    '与 `.note-select-card*`（+ `ProjectNotesList.tsx`、`Projects.test.tsx`）不在清单里 ——\n' +
    '判据（grep 出的文件数）写在 `src/styles/assessment.css` 文件头。\n\n' +
    parts4.join('\n\n'),
)

// 第四批的**冲突裁决表**：16 条（14 + 2）属性的实测胜者。
// 这不是"迁移前清单"，而是"哪个值真的在浏览器里生效"的实测记录 ——
// 一次性探针（真实 Chromium + 真实渲染路径）用完已删，配方见计划 §5 雷区 3，
// 所以这里把逐条结果固化下来，供后来人核对（不要手改：改的是历史证据）。
write(
  '5.6-09-conflict-resolution.md',
  '5.6 序 5：assessment.css × refinements.css 的 16 条属性冲突 —— 真实 Chromium 实测裁决',
  [
    '## 为什么必须实测',
    '',
    '`assessment.css` 与 `refinements.css` 对同一批选择器写了**不同的值**，',
    '两者权重相同（都是单类），于是"谁生效"**只取决于 `main.tsx` 的导入顺序**。',
    '读源码推不出来（两边都"在"），看产物文本也要靠人肉比字节位置。',
    '所以用仓库里现成的 Playwright（真实 Chromium）读 **computed style**：',
    '',
    '探针走的是**真实渲染路径**（不是往空页面塞 HTML）：',
    '登录 → `/assessment` → 已链接对比模式「开始评估」拿到 `.score-bar*` /',
    '`.knowledge-points-*` → 切「开放性问题」→ 选资料 →「生成问题」拿到',
    '`.quiz-question-card` / `-number` / `-text` / `textarea` → hover（真实指针）→',
    'focus（真实焦点）→「提交答案」拿到 `.score-summary-*`；',
    '`.card-hover:hover` 用注入的 `article.card.card-hover` + 真实 hover（它对祖先无依赖）。',
    '',
    '探针自身的四个坑（都踩过，配方里必须记着）：',
    '',
    '1. **必须等过渡结束再读**：第一次读到的是过渡中间值 ——',
    '   非 hover 态的 `box-shadow` 读到 `0 2.7px 9.4px rgba(…,.067)`（从 hover 值回落的 64% 处），',
    '   `.card-hover:hover` 的 `transform` 读成 `matrix(1,0,0,1,0,0)`（过渡起点），',
    '   两者都会让人误判成"两条规则都没生效"。每处状态变化后等 600ms，',
    '   `.score-bar-fill` 的 `width` 过渡是 0.6s，等 900ms。',
    '2. **`E2E_PORT` 换端口**（4321）：本机 4319 可能被另一个 agent 的 e2e 占着。',
    '3. **探针要在迁移前后都跑，选择器必须两边都能命中**：迁移前是全局 kebab 名',
    '   （`.quiz-question-card`）、迁移后是哈希的 camelCase 名（`._quizQuestionCard_hash`），',
    '   所以用 `:is([class*="quiz-question-card"], [class*="quizQuestionCard"])`。',
    '   ⚠️ **必须用 `:is()` 包住整个备选列表**：写成 `[class*="a"], [class*="b"] textarea`',
    '   时，后面的 ` textarea` 只作用于**最后一个**备选，第一个备选会匹配到卡片本身 ——',
    '   `fill()` 一个 `div` 会报 "Element is not an `<input>`"（实测踩到）。',
    '4. **探针落盘 JSON 别用 `node:fs`**：本项目没有 `@types/node`，而 `e2e/` 在',
    '   `tsconfig.json` 的 `include` 里 ⇒ `tsc`（`npm run build` 的第一步）直接红；',
    '   `testInfo.attach` 的 body 在**通过**的用例里不落盘；可行的是',
    '   `page.waitForEvent("download")` + `download.saveAs(path)`（Playwright 自己写文件）。',
    '',
    '## 结论（16 条全部由补丁层取胜）',
    '',
    '两个文件都在 `main.tsx` 里静态引入，`refinements.css` 在第 39 行（最后），',
    '`assessment.css` 在第 36 行：**后加载的补丁层赢**，与"补丁层"这个定位相符。',
    '于是 5.6 的搬运写的是胜者的值，输的那套（`assessment.css` 的 14 条 +',
    '`components.css` 的 2 条）已删除 —— 删的是永远不生效的死声明。',
    '',
    '### `.quiz-question-card`（5 条）',
    '',
    '| 属性 | 输（assessment.css） | 赢（refinements.css） | 实测 computed |',
    '|---|---|---|---|',
    '| `border-radius` | `var(--radius-md)` = 10px | `var(--radius-lg)` = 16px | **16px** |',
    '| `padding` | `var(--space-lg)` | `24px` | **24px**（两侧数值相同，写法之争） |',
    '| `margin-bottom` | `var(--space-md)` | `16px` | **16px**（两侧数值相同） |',
    '| `box-shadow` | `var(--shadow-sm)`（双层 rgba(26,26,46)） | `0 2px 8px rgba(15,52,96,.06)` | **rgba(15,52,96,.06) 0 2px 8px** |',
    '| `transition` | `border-color .2s ease` | `border-color .25s …, box-shadow .25s …` | **property=border-color,box-shadow；duration=.25s,.25s** |',
    '',
    '`refinements.css` 独有的声明（不是冲突，随规则一起进模块）：',
    '`border-left: 4px solid var(--color-border)`、`:hover` 的 `border-left-color` + 阴影、',
    '`:focus-within`、预留类 `.quiz-question-card-active`。',
    '',
    '### `.quiz-question-number`（6 条）',
    '',
    '| 属性 | 输 | 赢 | 实测 computed |',
    '|---|---|---|---|',
    '| `width` / `height` | `28px` | `32px` | **32px / 32px** |',
    '| `border-radius` | `9999px` | `50%` | **50%** |',
    '| `background` | `var(--gradient-primary)` | `var(--color-accent-light)` | **background-image=none；color=rgba(201,169,89,.12)** |',
    '| `color` | `white` | `var(--color-accent)` | **rgb(201,169,89)** |',
    '| `font-size` | `0.8rem` | `0.9rem` | **14.4px** |',
    '',
    '### `.quiz-question-text`（3 条）',
    '',
    '| 属性 | 输 | 赢 | 实测 computed |',
    '|---|---|---|---|',
    '| `align-items` | `center` | `flex-start` | **flex-start** |',
    '| `gap` | `var(--space-xs)` = 4px | `var(--space-sm)` = 8px | **column-gap 8px** |',
    '| `font-size` | `0.95rem` = 15.2px | `1.125rem` = 18px | **18px** |',
    '',
    '### `.card-hover:hover`（2 条，`components.css` × `refinements.css`）',
    '',
    '| 属性 | 输（components.css） | 赢（refinements.css） | 实测 computed |',
    '|---|---|---|---|',
    '| `transform` | `translateY(-1px)` | `translateY(-2px)` | **matrix(1,0,0,1,0,-2)** |',
    '| `box-shadow` | `var(--shadow-md)` | `var(--shadow-lg)` | **rgba(26,26,46,.08) 0 8px 24px, rgba(26,26,46,.04) 0 4px 8px** |',
    '',
    '第三条 `border-color: var(--color-border)` 两边**值相同**，不是冲突；',
    '`components.css` 里保留它、删掉那两条输的声明（逐字见该文件里的注释）。',
    '`.card-hover` 被 8 个页面使用（规范 §4 第 2 条），所以它**不搬进模块**，',
    '胜者也暂时留在补丁层：等序 12 拆 `refinements.css` 时再连同归属一起收口。',
    '',
    '### 另一类"半覆盖"：`.score-summary-number`（5 条，不是同名属性冲突）',
    '',
    '`refinements.css` 用 `.score-summary-card .score-summary-number`（权重 (0,2,0)）',
    '压掉单类规则里的字号/配色/背景裁剪。合并成单类会把权重降到 (0,1,0)',
    '（第三批 `.list-toolbar .search-input-wrapper` 被写成单类时正是被差集抓到的），',
    '所以模块里**故意保留两条规则**，只删掉单类里被压掉的那 5 条：',
    '',
    '| 属性 | 输（`.score-summary-number` 单类） | 赢（(0,2,0) 那条） | 实测 computed |',
    '|---|---|---|---|',
    '| `font-size` | `2.5rem` = 40px | `2rem` = 32px | **32px** |',
    '| `background` | `var(--gradient-primary)` | `none` | **background-image: none** |',
    '| `-webkit-background-clip` | `text` | `unset` | **background-clip: border-box** |',
    '| `background-clip` | `text` | `unset` | **border-box** |',
    '| `-webkit-text-fill-color` | `transparent` | `var(--color-accent)` | **color: rgb(201,169,89)** |',
    '',
    '留下来的是单类里没被压掉的 `font-family` / `font-weight` / `line-height` / `margin-bottom`',
    '（实测 `line-height: 32px`、`margin-bottom: 4px` 都来自那一条）。',
    '',
    '## 迁移后怎么证明"渲染结果没变"',
    '',
    '同一个探针跑两次，逐元素比**全部** computed 属性（不是只挑那 16 条）：',
    '',
    '1. **迁移前**：`git worktree add --detach <临时目录> HEAD`（HEAD 里还没有本批改动）',
    '   + 把 `node_modules` 用 junction 接过去，在该工作树里起 dev server（4322）；',
    '   探针 `PROBE_BASE=http://127.0.0.1:4322` 跑一次，导出 31 个元素共 **16 802** 条属性。',
    '   （用 worktree 而不是 `git stash` 回退：本仓库同时有别的 agent 在改，',
    '   回退工作区会把两边未提交的改动搅在一起，而 worktree 只读 HEAD、不碰工作区。）',
    '2. **迁移后**：同一个探针跑在当前工作区（dev server 4319），',
    '   把上一次导出的 JSON 用 `route.fulfill({ path })` 喂回页面里做逐属性比对。',
    '',
    '**结果：16 802 条 computed 属性，差异 0 条**（页面内比对与 node 侧独立比对各跑一遍，都是 0）。',
    '只有 class 属性按预期变了：**18 个被测元素拿到哈希类名**（模块确实生效了），',
    '**13 个一字不变** —— 留全局的 `.assessment-*` / `.note-select-card*`、',
    '元素选择器命中的 `textarea` / `li`（本来就没有类名），以及全局冲突裁决后的 `.card-hover`。',
    '',
    '之所以导出**全部**属性而不是只挑那 16 条：computed style 是级联求解后的结果，',
    '简写 vs 长写（计划 §5 雷区 12 的盲区）会自动体现在解析出来的长写上。',
    '',
    '探针还顺带在真实浏览器里钉了两条**窄屏**断言（jsdom 不认 `@media`，只有真浏览器能证）：',
    '420px 视口下 `.knowledge-points-grid` 的 `grid-template-columns` 解析成**一条轨道**',
    '（= 模块里那条 480px 规则生效），1280px 下是**两条**；迁移前后两次运行都过。',
    '⚠️ `setViewportSize` 之后必须等一次重排（400ms）再读：第一次没等，读到的是重排途中的',
    '中间值（274.7px / 130px，而正确值是 370px = 420 − 2×8 容器内边距 − 2×16 卡片内边距 − 2 边框）。',
    '那种"差异"全是测量竞态，与迁移无关 —— 这也是为什么要用 worktree 把两侧测法统一后再比。',
  ].join('\n'),
)

// ── 5. 第三批的"停"：markdown.css 的 `.adhd-*` 留全局（留一份可核对的清单）──
// 这不是"迁移前"清单，而是"核对后决定不动"的清单：把 5 条规则的原文留下来，
// 下一个读代码的人可以直接对照 `docs/css-migration-plan.md` §7.1 的判据。
const adhd = run('css-rule-inventory.mjs', [
  'src/styles/markdown.css',
  '--class',
  'adhd-reader-active,adhd-block,adhd-current-block,adhd-line-marker',
  '--from-git',
])
if (adhd.code !== 0) throw new Error(`清单生成失败（markdown.css）：${adhd.out}`)
write(
  '5.6-07-markdown-adhd-stayed-global.md',
  '5.6 第三批：markdown.css 的 `.adhd-*` 规则 —— 逐处核对写入点后**留全局**',
  '> ⚠️ **本文件是第三批当时的历史结论**（"停"）。序 6 收尾轮已经把这条"停"收掉了：\n' +
    '> 那 5 条规则进了 `src/hooks/useAdhdReader.module.css`，类名改由模块导出。\n' +
    '> 收尾后的证据在 `5.6-14-adhd-moved-to-module.md`；本文下面的理由**逐条都还成立**，\n' +
    '> 变的只有一条：第三条理由里"写入点所在的 `src/hooks/**` 不在本批可改范围"\n' +
    '> —— 那是**范围**问题，人类批准把 hook 纳入范围之后它就不成立了。\n\n' +
    '**本批没有搬 `markdown.css` 的任何一条规则**（计划 §7.1 事先标了"可能是个停"）。\n' +
    '下面这份清单是"决定不动"的那 5 条规则原文，留作核对。\n\n' +
    '## 为什么不动\n\n' +
    '1. 5 条规则**全部**是 `.markdown-body.adhd-reader-active …` 的后代选择器，\n' +
    '   而 `.markdown-body` 自己按判据必须留全局：三个**不相邻**功能在用它\n' +
    '   （`pages/notedetail/MarkdownReader.tsx`、`pages/notedetail/EditSplitView.tsx`、\n' +
    '   `components/NoteAskPanel.tsx` —— 后者还 `document.querySelector(".markdown-body")`），\n' +
    '   正文 HTML 又来自 `marked` 渲染的字符串（`css-convention.md` §4 第 2、4 条）。\n' +
    '2. 四个 `.adhd-*` 类名的**全部写入点**都在 `src/hooks/useAdhdReader.ts`\n' +
    '   （`classList.add/remove/contains` 与一次 `className = "adhd-line-marker"` 整体赋值）——\n' +
    '   逐处表见 `src/styles/markdown.css` 文件头。三条判据逐条对照：\n' +
    '   "改成语义查询"不行（这些类名是给 `marked` 生成的块打标记的唯一手段）、\n' +
    '   "从模块导出常量"不行（写入点所在的 `src/hooks/**` 不在本批允许改动的文件范围内，\n' +
    '   且为 4 个类名让通用 hook 去 import 页面模块等于颠倒归属）、\n' +
    '   "留全局"成立（第 1 条已给出判据依据）。\n' +
    '3. `utils/markdown.ts` 不注入这些类名（grep 0 命中）；`responsive.css`\n' +
    '   对 `.adhd-*` 也 0 命中 —— 不存在"补丁层还在命中它"的问题。\n\n' +
    '⚠️ 硬塞进模块只能写成 `:global(.adhd-block)` 之类，那等于一个字符都没被\n' +
    '作用域化，却把"这些类名是全项目约定"藏进一个页面模块里（规范 §4 末尾的"半搬"）。\n\n' +
    `\`\`\`\n${adhd.out.trim()}\n\`\`\``,
)

// ── 11. 序 6 收尾：第三批那个"停"被收掉了 —— `.adhd-*` 进 hook 模块 ──
// 迁移**前**的清单仍然从 git 读（提交之后 `findRecentRev` 会按内容自动往回收一格）。
const adhdMoved = run('css-rule-inventory.mjs', [
  'src/styles/markdown.css',
  '--class',
  'adhd-reader-active,adhd-block,adhd-current-block,adhd-line-marker',
  '--from-git',
])
if (adhdMoved.code !== 0) throw new Error(`清单生成失败（markdown.css，序 6）：${adhdMoved.out}`)
write(
  '5.6-14-adhd-moved-to-module.md',
  '5.6 序 6 收尾：markdown.css 的 5 条 `.adhd-*` 规则进 `src/hooks/useAdhdReader.module.css`',
  '这是 5.6 的**最后一处"有记录的停"**（第三批判的，见 `5.6-07`）被收掉的记录。\n\n' +
    '## 1. 四个类名的**全部**写入点（改之前逐处枚举过，改之后逐处核对）\n\n' +
    '`grep -rn "adhd-" frontend/src`（含模板串与 `utils/markdown.ts`）只有**一个**文件命中：\n' +
    '`src/hooks/useAdhdReader.ts`。逐处清单与改法：\n\n' +
    '| 改前（行号是改之前的） | 操作 | 改后 |\n' +
    '|---|---|---|\n' +
    '| `:36` `classList.contains(\'adhd-line-marker\')` | 读（判断该元素是不是标记条） | `styles.adhdLineMarker` |\n' +
    '| `:165` `marker.className = \'adhd-line-marker\'` | 整体赋值 | `styles.adhdLineMarker` |\n' +
    '| `:216` / `:274` `classList.remove(\'adhd-current-block\')` | 移除当前块 | `styles.adhdCurrentBlock` |\n' +
    '| `:217` `classList.add(\'adhd-current-block\')` | 换行时切换当前块 | `styles.adhdCurrentBlock` |\n' +
    '| `:266` `classList.add(\'adhd-block\')` | 给每个 `marked` 顶层块加 | `styles.adhdBlock` |\n' +
    '| `:269` / `:272` `classList.add/remove(\'adhd-reader-active\')` | 开关阅读模式 | `styles.adhdReaderActive` |\n\n' +
    '改后这 8 处的行号是 `:47` / `:176` / `:227` / `:228` / `:277` / `:280` / `:283` / `:285`；\n' +
    '`src/` 下这四个类名的**字面量命中 0 处**（`markdown.css` 里只剩墓碑注释，注释不算声明）。\n' +
    '`utils/markdown.ts` **再次核实 0 命中**（计划里那个"可能"两次都被排除）；\n' +
    '`responsive.css` / `refinements.css` 对 `.adhd-*` 也是 0 命中，不存在"补丁层还在命中它"。\n\n' +
    '## 2. 为什么现在能搬（第三批那三条理由的现状）\n\n' +
    '| 第三批的理由 | 现状 |\n' +
    '|---|---|\n' +
    '| ①"改成语义查询"不行 | **仍然不行**（这些类名是给 `marked` 生成的块打标记的唯一手段），但这条不是唯一出路 |\n' +
    '| ②"从模块导出常量"不行 —— 写入点 `src/hooks/**` 不在本批可改范围 | **不成立了**：那是范围问题，hook 已纳入范围；且模块就放在 hook 自己的目录里，没有"通用 hook import 页面模块"的归属颠倒 |\n' +
    '| ③"留全局"成立 | 这条判的是 **`.markdown-body`**，而它**仍然留全局**（规范 §4 第 2、4 条），进模块的只有本 hook 自己的 4 个类名 |\n\n' +
    '⚠️ 与规范 §4 末尾点名的"半搬"（`:global(.adhd-block)`：本模块自己的类名写成全局，\n' +
    '一个字符都没被作用域化）的区别：模块里 4 个类名**全是本地类**（产物 `._adhdBlock_hash`），\n' +
    '只有 `.markdown-body` 写成 `:global(...)` —— 那是**引用**一个本来就该全局的类名，\n' +
    '与 `App.module.css` 的 `.appLayout :global(.container)`、`NoteDetailHeader.module.css` 的\n' +
    '`.noteDetailActions :global(.btn)` 是同一种写法。\n\n' +
    '**权重逐字未变**：`:global(.markdown-body).adhdReaderActive` 与\n' +
    '`.markdown-body.adhd-reader-active` 同为 (0,2,0)，另三条 (0,2,0) / (0,3,0) / (0,3,1) 同理。\n' +
    '产物位置从 `index.css`（全局样式表第 3 个）变成懒加载的 `NoteDetail-*.css`\n' +
    '（`__vitePreload` 仍在 `index.css` 之后注入），而这 5 条没有任何同属性同权重的竞争对手 ⇒\n' +
    '一条声明的胜负都不会翻转。\n\n' +
    '## 3. 三条机器证据\n\n' +
    '1. **规则清单差集**（`5.6-02-rule-diff.md`）：本批 **逐字保留 5 / 值有变化 0 / 丢失 0**。\n' +
    '   ⚠️ 工具这一轮补了一条归一化：压缩器会**压掉组合器两侧的空白**\n' +
    '   （源码 `.a > .b` → 产物 `._a_h>._b_h`），不归一化会把 4 条带 `>` 的规则整条报成"丢失"\n' +
    '   —— 这是本仓库第一次迁移带子组合器的规则（`neutralSelector`，规范 §7 第 11 条）。\n' +
    '2. **产物校验**（`5.6-03-built-css.md`）：切片标记 `adhdReaderActive` / `adhdBlock` /\n' +
    '   `adhdCurrentBlock` / `adhdLineMarker` 在产物里各命中 5 / 3 / 2 / 1 次；\n' +
    '   四个 kebab 类名进 `RETIRED` 后命中 **0**；悬空动画 0；改动范围内冲突 0；\n' +
    '   级联得主与媒体查询覆盖战、简写 vs 长写全部照旧。\n' +
    '3. **真 Chromium 渲染对账**（一次性探针，用完已删；配方见计划 §5 雷区 3）：\n' +
    '   两侧都用**产物**（`vite preview`）：迁移前 = `git worktree add --detach <tmp> HEAD`\n' +
    '   + `npx vite build`（只读 HEAD，不碰工作区）；迁移后 = 当前工作区的 `dist`。\n' +
    '   探针按 **DOM 结构**取样（不按类名 —— 类名哈希后按类名取样会让两侧取到不同元素）：\n' +
    '   容器 `.markdown-body` + 它的 6 个子元素（5 个正文块 + 行级标记条）+ 1 个链接，\n' +
    '   共 **8 个样本 × 全部 computed 属性 = 4 328 条值**（不是只挑那几条）。\n' +
    '   实测：**未声明的差异 0 条**；class 属性按预期 **7 个变哈希 / 1 个一字不变**\n' +
    '   （那个链接本来就没有类名），行内 `style`（渐变模糊与标记条定位由 JS 写入）**逐字相同**。\n' +
    '   探针自身的三个坑都处理了：块切换有 3 帧滞回（一次 `mousemove` 不够）、\n' +
    '   必须等过渡结束（≥600ms）、落盘不能用 `node:fs`（`e2e/` 在 `tsconfig` 的 include 里，\n' +
    '   而本项目没有 `@types/node`）—— 这条与计划 §5 雷区 18 记的是同一批坑。\n\n' +
    '## 4. 迁移前的那 5 条规则原文\n\n' +
    `\`\`\`\n${adhdMoved.out.trim()}\n\`\`\``,
)

// ── 7. 序 8 / 9 / 10 的迁移前清单（第五、六、七批） ──
const batch789Sheets = [
  {
    title: '序 8：components.css 的页面级布局挂点（+ responsive.css 同批窄屏规则）',
    sheets: [
      { sheet: 'src/styles/components.css', classes: ['note-detail-header', 'note-detail-actions', 'note-list-item', 'note-list-actions', 'edit-split'] },
      { sheet: 'src/styles/responsive.css', classes: ['note-detail-header', 'note-detail-actions', 'note-list-item', 'note-list-actions', 'edit-split'] },
    ],
  },
  {
    title: '序 9：layout.css 按归属拆分（Sidebar / App 骨架）+ responsive.css 同批规则',
    sheets: [
      {
        sheet: 'src/styles/layout.css',
        classes: [
          'sidebar', 'sidebar-collapsed', 'sidebar-mobile-open', 'sidebar-header', 'sidebar-logo',
          'sidebar-collapse-btn', 'sidebar-mobile-close', 'sidebar-body', 'sidebar-open-lock',
          'sidebar-section', 'sidebar-section-title', 'sidebar-item-row', 'sidebar-item',
          'sidebar-item-active', 'sidebar-item-icon', 'sidebar-item-label', 'sidebar-item-action',
          'sidebar-divider', 'sidebar-footer', 'sidebar-overlay',
          'app-layout', 'app-layout-collapsed', 'sidebar-mobile-toggle',
        ],
      },
      { sheet: 'src/styles/responsive.css', classes: ['sidebar', 'sidebar-mobile-open', 'app-layout', 'sidebar-mobile-toggle', 'sidebar-item', 'sidebar-item-action', 'sidebar-section-title', 'sidebar-collapse-btn', 'sidebar-mobile-close'] },
    ],
  },
  {
    title: '序 10：graph.css 整份进图谱功能模块 + responsive.css 同批规则',
    sheets: [
      {
        sheet: 'src/styles/graph.css',
        classes: [
          'graph-page', 'graph-page-main', 'graph-sidebar', 'graph-canvas', 'graph-toolbar',
          'graph-toolbar-left', 'graph-toolbar-right', 'graph-legend', 'graph-legend-item',
          'graph-legend-item-active', 'graph-legend-dot', 'graph-btn', 'graph-btn-active',
          'graph-badge', 'graph-create-hint', 'graph-panel', 'graph-panel-title',
          'graph-suggestion-card', 'graph-relation-line', 'graph-controls', 'graph-control-btn',
          'graph-minimap', 'graph-suggestion-score-bar', 'graph-suggestion-score-bar-fill',
          'graph-stats-grid', 'graph-stat-item', 'graph-stat-value', 'graph-stat-label',
          'graph-stats-bar-row', 'graph-stats-bar-label', 'graph-stats-bar-track',
          'graph-stats-bar-fill', 'graph-stats-bar-count', 'graph-search-box',
          'graph-search-input', 'graph-search-spinner', 'graph-search-results',
          'graph-search-result-item', 'graph-search-result-dot', 'graph-search-result-title',
          'graph-search-result-type', 'graph-filter-select', 'graph-neighbor-item',
          'graph-neighbor-dot', 'graph-neighbor-title', 'graph-neighbor-rel', 'graph-neighbor-count',
        ],
      },
      { sheet: 'src/styles/responsive.css', classes: ['graph-page', 'graph-toolbar', 'graph-toolbar-left', 'graph-toolbar-right', 'graph-search-box', 'graph-search-input', 'graph-sidebar', 'graph-minimap', 'graph-controls', 'graph-control-btn', 'graph-filter-select', 'graph-btn'] },
    ],
  },
]
const parts789 = []
for (const group of batch789Sheets) {
  const blocks = []
  for (const s of group.sheets) {
    const r = run('css-rule-inventory.mjs', [s.sheet, '--class', s.classes.join(','), '--from-git'])
    if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
    blocks.push(`### ${s.sheet}\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
  }
  parts789.push(`## ${group.title}\n\n${blocks.join('\n\n')}`)
}
write(
  '5.6-10-before-batches-8-9-10.md',
  '5.6 迁移前：序 8 / 序 9 / 序 10（components 挂点 → Sidebar/App 骨架 → 图谱）的规则清单',
  '每一组都同时列出**全局样式表那半**与 **`responsive.css` 那半** —— 这两半必须同批\n' +
    '（类名一旦哈希，留在补丁层里的选择器永远选不中，计划 §5 雷区 2）。\n' +
    '修订由 `css-rule-inventory.mjs` **按内容自动定位**（从 HEAD 往回找第一个还含有\n' +
    '这些类名的提交），所以清单头部的 `@HEAD~N` 会随提交数变化，规则内容不会。\n\n' +
    '⚠️ 收尾轮（死代码清理）之后，`layout.css` 与 `graph.css` 也只剩注释了：\n' +
    '前者的 5 条 `.navbar*`（旧顶部导航栏，全项目 grep 0 处引用）、后者的 `@keyframes graph-spin`\n' +
    '（动画体早已逐字复制成模块里的 `graphSpin`）都已删除 —— 见计划 §7.0 第 2 条与\n' +
    '`docs/migration-evidence/5.6-13-dead-css-cleanup.md`。\n\n' +
    parts789.join('\n\n'),
)

// ── 8. 序 12 / 13 的迁移前清单 + **两张补丁层清空的逐条去向审计** ──
const batch1213Sheets = [
  {
    sheet: 'src/styles/refinements.css',
    classes: [
      'markdown-editor', 'edit-toolbar', 'btn', 'card-hover', 'filter-pill', 'filter-pill-active',
      'segment-btn', 'segment-btn-active', 'card', 'link-modal', 'material-list-item',
      'material-list-item-selected', 'type-badge', 'type-badge-material',
    ],
  },
  {
    sheet: 'src/styles/responsive.css',
    classes: [
      'btn', 'filter-pill', 'segment-btn', 'btn-ghost', 'card', 'page-header-row',
      'markdown-body', 'heading-serif',
    ],
  },
]
const parts1213 = []
for (const s of batch1213Sheets) {
  const r = run('css-rule-inventory.mjs', [s.sheet, '--class', s.classes.join(','), '--from-git'])
  if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
  parts1213.push(`### ${s.sheet}\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}

/**
 * 两张补丁层清空后的**逐条去向审计**（自包含，不依赖 BATCHES 表）：
 * 对迁移前那份样式表里的每一条规则，在工作区的**全部** CSS 里找一条
 * "上下文相同 + 选择器相同（类名 kebab→camel 归一）+ 声明逐字相同"的规则。
 * 找不到的必须落在下面的显式例外表里（每条都要写清依据），否则报错退出。
 */
const EMPTY_SHEET_EXCEPTIONS = [
  {
    // 13 条属性选择器：真 Chromium 实测命中 0 个元素（浏览器把内联 rgba 序列化成带空格的形式）
    match: (sel) => sel.startsWith('[style*="rgba(0,0,0,0.5)"]'),
    why: '实测选择器命中 0 个元素（迁移前就从未生效）⇒ 已删除；见探针输出与 BATCHES[].resolvedConflicts',
  },
  {
    match: (sel) => sel === ':root',
    why: '`--page-pad-y-*` 两条自定义属性随 `.app-layout` 收进 App.module.css 的 `.appLayout`（序 9，选择器变了）',
  },
  {
    // 收尾轮（死代码清理）：5 个零引用的"预留语义化类"（6 条规则，含 `:hover`）
    match: (sel) =>
      [
        '.link-modal',
        '.material-list-item',
        '.material-list-item:hover',
        '.material-list-item-selected',
        '.type-badge',
        '.type-badge-material',
      ].includes(sel),
    why:
      '收尾轮死代码清理：这 5 个类是零引用的"预留语义化类"（`LinkManagerModal.tsx` 用的是内联 style，' +
      '一个类名都没挂；`src/**` 的 TSX/TS grep 0 处、运行时拼类名 0 处）⇒ 删除，' +
      '登记在 `css-migration-diff.mjs` 第八批的 `resolvedConflicts` 与 ' +
      '`verify-built-css.mjs` 的 `CLEANUP_RETIREMENTS`；证据 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`',
  },
]

function normalizedSelector(sel) {
  return sel
    // `:global(.container)` → `.container`（模块里保住全局类名的写法，产物里就是 `.container`）
    .replace(/:global\(([^)]*)\)/g, '$1')
    .replace(/\s+/g, ' ')
    .replace(/\.([a-z][\w-]*)/gi, (_, name) => `.${name.replace(/-([a-z0-9])/g, (m, c) => c.toUpperCase()).toLowerCase()}`)
    .trim()
}
function declList(decls) {
  return decls.map(([p, v]) => `${p.trim().toLowerCase()}:${v.replace(/\s+/g, ' ').trim()}`)
}
function normalizedDecls(decls) {
  return declList(decls).sort().join(';')
}

const workspaceSheets = []
;(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.isDirectory()) walk(path.join(dir, e.name))
    else if (e.name.endsWith('.css')) workspaceSheets.push(path.join(dir, e.name))
  }
})(path.join(root, 'src'))
const workspaceRules = workspaceSheets.flatMap((abs) =>
  parseRules(fs.readFileSync(abs, 'utf8')).map((r) => ({
    file: path.relative(root, abs).replace(/\\/g, '/'),
    context: r.context,
    members: splitSelectors(r.selector).map(normalizedSelector),
    decls: declList(r.decls),
  })),
)

/**
 * 一条规则算"找到了"的判据（三条缺一不可）：
 *   1. **每个选择器成员**都能在工作区里找到一条同上下文的规则**包含它** ——
 *      这允许"选择器组被拆开"（`.filter-pill, .segment-btn, .graph-btn` 拆成
 *      留全局的一半 + 进模块的一半，计划里两次都登记了）；
 *   2. 那条规则**包含本条规则的全部声明**（逐字，空白归一）；
 *   3. 找不到就落进显式例外表，否则计入"静默丢失"并让脚本失败。
 */
function findRule(rule) {
  const members = splitSelectors(rule.selector).map(normalizedSelector)
  const decls = declList(rule.decls)
  return members.every((m) =>
    workspaceRules.some((w) => w.context === rule.context && w.members.includes(m) && decls.every((d) => w.decls.includes(d))),
  )
}

const auditLines = []
let auditFailed = false
for (const sheet of ['src/styles/responsive.css', 'src/styles/refinements.css']) {
  const rev = findRecentRev((candidate) => parseRules(readFromGit(path.join(root, sheet), candidate)).length > 5)
  if (!rev) throw new Error(`审计：找不到 ${sheet} 还有内容的修订`)
  const rules = parseRules(readFromGit(path.join(root, sheet), rev))
  let inPlace = 0
  const excepted = []
  const missing = []
  for (const r of rules) {
    if (findRule(r)) {
      inPlace += 1
      continue
    }
    const ex = EMPTY_SHEET_EXCEPTIONS.find((e) => e.match(r.selector.trim()))
    if (ex) {
      excepted.push(`${r.context || '(顶层)'} \`${r.selector}\` —— ${ex.why}`)
      continue
    }
    missing.push(`${r.context || '(顶层)'} \`${r.selector}\` { ${r.decls.map(([p]) => p).join(', ')} }`)
  }
  auditLines.push(
    `### ${sheet}（迁移前 ${rev} 有 ${rules.length} 条规则）`,
    '',
    `- **逐字在工作区里找到**：${inPlace} 条`,
    `- **按显式例外处理**：${excepted.length} 条`,
    ...excepted.map((x) => `    - ${x}`),
    `- **既找不到也没有例外（= 静默丢失）**：${missing.length} 条`,
    ...missing.map((x) => `    - ✗ ${x}`),
    '',
  )
  if (missing.length) auditFailed = true
}

write(
  '5.6-11-before-batches-12-13-and-empty-patch-layers.md',
  '5.6 迁移前：序 12 / 序 13（两张补丁层清空）的规则清单 + 逐条去向审计',
  '`refinements.css` 与 `responsive.css` 是两层"后加载覆盖前面"的补丁，' +
  '**清空它们**（而不是删文件 —— `mobile-input-font-size.test.ts` 断言每个\n' +
    '`src/styles/*.css` 都被 `main.tsx` 引入）是 5.6 的最后两步。\n' +
    '这一批的证据不是"逐条保留"（规则只是换了归属），而是**去向审计**：\n' +
    '迁移前那份样式表里的每一条规则，都能在工作区的某个 CSS 里找到\n' +
    '"上下文 + 选择器（类名 kebab→camel 归一）+ 声明逐字相同"的那一条，\n' +
    '或者落在显式例外表里（每条都写明依据）。\n\n' +
    '例外有三类：\n' +
    '1. **13 条属性选择器 `[style*="rgba(0,0,0,0.5)"] …`**：真 Chromium 实测该选择器\n' +
    '   命中 **0** 个元素（React 走 CSSOM 赋内联值，浏览器把它序列化成带空格的\n' +
    '   `rgba(0, 0, 0, 0.5)`），也就是说这些规则**迁移前就从未生效**，删它是可证明的空操作；\n' +
    '2. **`:root` 的 `--page-pad-y-*`**：随 `.app-layout` 收进 `App.module.css` 的 `.appLayout`\n' +
    '   （选择器变了，值一字未改；`relocations` 里有双向自检）；\n' +
    '3. **5 个零引用的"预留语义化类"**（`.link-modal` / `.material-list-item*` / `.type-badge*`，6 条规则）：\n' +
    '   序 12 逐字搬进 `components.css`，收尾轮（死代码清理）**整条删除** ——\n' +
    '   `src/**` 的 TSX/TS grep 0 处、运行时拼类名 0 处，`LinkManagerModal.tsx` 用的是内联 style。\n' +
    '   证据 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`。\n\n' +
    '## 去向审计结果\n\n' +
    auditLines.join('\n') +
    '\n## 迁移前清单（按类名分组）\n\n' +
    parts1213.join('\n\n'),
)

// ── 9. 序 8~13 的"渲染没变"实测（真 Chromium 探针，迁移前后各跑一次） ──
// 探针是一次性的（`e2e/zz-cssprobe-*.spec.ts` + 临时 worktree），用完已删 ——
// 结论必须固化在这里，否则它只活在某个人的对话里。
write(
  '5.6-12-rendered-unchanged-probe.md',
  '5.6 序 8~13：真实 Chromium 的 computed style 对账（迁移前 worktree HEAD vs 迁移后工作区）',
  [
    '## 怎么量的（配方见 `css-convention.md` §7 / 计划 §5 雷区 3）',
    '',
    '1. `git worktree add --detach D:\\engramnote-cssprobe-head HEAD`（**只读 HEAD，不碰工作区** ——',
    '   仓库里有并行 agent，`git stash` 会把别人的改动一起搅进来）+ `node_modules` junction，',
    '   在该工作树里起独立 dev server（4321）。',
    '   ⚠️ 必须给 worktree 一个**独立的 vite 缓存目录**（`cacheDir`）：与工作区共用一个',
    '   `node_modules/.vite` 时两边会互相覆盖预打包产物，症状是图谱页崩到错误边界',
    '   （`Cannot read properties of null (reading \'useRef\')` —— react-force-graph 拿不到 React）。',
    '2. **录制**：10 个场景（`/notes` 桌面/窄屏/抽屉打开/hover 卡片、`/notes/note-1` 桌面/窄屏/编辑态/',
    '   关联资料弹窗、`/graph` 桌面/窄屏），每个场景等"就绪元素可见"再等 400ms 排版稳定，',
    '   读 **54 条 computed 属性**；hover 场景等 700ms 让过渡结束（不等会读到中间值）。',
    '3. **比对**：同一份探针跑在当前工作区，把上一次的 JSON 用 `route.fulfill({ path })` 喂回页面，',
    '   逐属性比。落盘/读取都**不用 `node:fs`**（本项目没有 `@types/node`）：',
    '   录制用 `download.saveAs()`，比对用 `route.fulfill({ path })`。',
    '',
    '## 结果',
    '',
    '| 场景 | 样本 × 属性 | 差异 | class 属性（哈希）变化 / 不变 |',
    '|---|---|---|---|',
    '| notes-desktop | 8 × 54 | **0** | 5 / 3 |',
    '| notes-mobile | 6 × 54 | **0** | 4 / 2 |',
    '| notes-mobile-drawer-open | 3 × 54 | 1（已声明：遮罩动画改名） | 3 / 0 |',
    '| notes-desktop-card-hover | 1 × 54 | **0** | 1 / 0 |',
    '| notedetail-desktop | 4 × 54 | **0** | 3 / 1 |',
    '| notedetail-mobile | 4 × 54 | **0** | 2 / 2 |',
    '| notedetail-edit-mobile | 2 × 54 | **0** | 2 / 0 |',
    '| notedetail-link-modal | 6 × 54 | **0** | 0 / 6 |',
    '| graph-desktop | 12 × 54 | **0** | 12 / 0 |',
    '| graph-mobile | 10 × 54 | **0** | 10 / 0 |',
    '',
    '**合计 56 个样本 × 54 条属性 = 3 024 条 computed 值；未声明的差异 0 条。**',
    '',
    '唯一一条差异是**已声明**的：`.sidebar-overlay` 的 `animation-name` 从 `fadeIn` 变成',
    '`_sidebarOverlayFadeIn_hash`。CSS Modules 会把 `@keyframes` 名一起哈希，而定义留在',
    '`base.css` ⇒ 产物里悬空、动画静默消失（计划 §5 雷区 1）。修法是把动画体逐字复制进',
    '`Sidebar.module.css` 并改名：动画体一致（差集脚本的"动画体比对"逐条核对）、',
    '引用与定义在同一产物文件里（`verify-built-css.mjs` 的"悬空动画引用 0 条"）。',
    '所以这一条是"必须改的名字"，不是回归。',
    '',
    '## 顺带量到的两件事',
    '',
    '### 1. `[style*="rgba(0,0,0,0.5)"]` 是死选择器（真浏览器实测）',
    '',
    '在真实打开「管理关联资料」弹窗的页面里统计：',
    '',
    '```',
    '[probe] link-modal 遮罩选择器命中：无空格=0 带空格=1 实际遮罩=1',
    '[probe] 遮罩 style 属性：position: fixed; inset: 0px; background: rgba(0, 0, 0, 0.5);',
    '        z-index: 1000; display: flex; align-items: center; justify-…',
    '```',
    '',
    '也就是说 `refinements.css` 的 12 条 + `responsive.css` 的 1 条属性选择器规则',
    '**迁移前就从未生效**。计划 §7.1 原来设想"先给弹窗一个真类名再搬"——',
    '实测证明那会**改变外观**（那 13 条突然开始生效），超出"证明什么都没丢"的契约，',
    '所以本轮按"实测从未生效 ⇒ 删除"处理，并逐条登记（`prop: \'*\'`）。',
    '',
    '### 2. 简写 vs 长写的跨属性竞争（计划 §5 雷区 12）在窄屏上真的存在',
    '',
    '`responsive.css` 的 `.filter-pill, .segment-btn { padding-left/right: var(--space-md) }`',
    '与 `refinements.css` 的 `@media 768 .filter-pill { padding: var(--space-xs) var(--space-sm) }`',
    '同上下文、同权重 ⇒ **后者的简写压掉前者的左右长写**。两条搬进 `learning.css` 时',
    '保持了"长写在前、简写在后"的顺序，探针的 `notes-mobile` 场景（含 `.filter-pill`）',
    '54 条属性差异 0 条，实测确认胜负关系没变。',
    '',
    '## 探针自身的两个坑（记下来，别重踩）',
    '',
    '1. **拼后代选择器必须用 `:is()`**：`[class~="a"],[class*="b_"] .btn` 是**两个**选择器',
    '   （`[class~="a"]` 或 `[class*="b_"] .btn`），第一个会把容器 div 自己匹配走 ——',
    '   于是 `actionButton` 对照的是"容器 vs 按钮"，26 条属性差异全是假的。',
    '   `:is([class~="a"],[class*="b_"]) .btn` 才是"a 或 b 里面的 .btn"。',
    '2. **`page.waitForEvent(\'download\')` 必须在触发之前注册**：下载事件可能在',
    '   `page.evaluate` 期间就发出，后注册会一直等到超时（第一次跑 9 个场景全卡在 90s 超时）。',
    '   另外用 Blob URL 而不是 `data:` URL 更稳。',
  ].join('\n'),
)

// ── 10. 收尾轮：死代码清理的证据 ──
// 这一轮的删除**不能靠"看起来没人用"**：每一条都要有"TSX/TS grep 0 处 +
// 运行时拼类名 0 处 + 产物里 0 次"三类证据，而"迁移前确实有它"这一侧
// 由 `verify-built-css.mjs` 的 `CLEANUP_RETIREMENTS` 机器自检（从 HEAD 往回
// 按内容找修订）。这里的原文是一次性扫描脚本的真实输出（用完已删）。
write(
  '5.6-13-dead-css-cleanup.md',
  '5.6 收尾轮：死 CSS 清理（逐条证据 + 三个方向的自检）',
  [
    '## 删了什么（4 组，共 21 个条目）',
    '',
    '| 组 | 条目 | 依据 |',
    '|---|---|---|',
    '| 旧顶部导航栏 | `layout.css` 的 6 条 `.navbar*` 规则（`.navbar` / `-logo`(+`:hover`) / `-links` / `-logout`(+`:hover`)）+ `base.css` 的 `--navbar-height` | TSX/TS 0 处引用（唯一命中是 `Sidebar.tsx` 里一句"替代原有顶部 Navbar"的注释）；`.navbar{display:none}` 本身说明视觉重构后它已经被藏掉了 |',
    '| 零引用的预留语义化类 | `components.css` 的 6 条规则（`.link-modal` / `.material-list-item`(+`:hover`) / `-selected` / `.type-badge` / `-material`） | TSX/TS 0 处引用；`LinkManagerModal.tsx` 用的是内联 style，一个类名都没挂 |',
    '| 没有用户的 `@keyframes` | `base.css` 7 个（`scaleIn` / `cleaning-pulse` / `glowPulse` / `slideDown` / `pulse` / `float` / `gradientShift`）+ `graph.css` 的 `graph-spin` + 模块里的 `cleaningPulse` | 前三个是迁移时"复制 + 改名"进模块后失去全局引用者（计划 §7.0 第 2、5 条）；`slideDown` / `pulse` / `float` / `gradientShift` **从来没有过用户**；`graphSpin` 同理 |',
    '| 模块里的死规则 | `CleaningPanel.module.css` 的 `.cleaningProgress` / `.cleaningProgressBar` | 清洗进度条改由 `TaskProgress` 用全局 `.progress-bar*` 渲染；全项目 0 处 TSX 引用（第一批"逐字搬来不删"时就记过账） |',
    '',
    '**合计**：登记表 21 个条目（`verify-built-css.mjs` 的 `CLEANUP_RETIREMENTS`，实测 21/21 通过）',
    '= **11 个类名**（覆盖 14 条规则：`.navbar*` 6 条、预留类 6 条、`.cleaningProgress*` 2 条）',
    '+ **9 个 `@keyframes` 定义**（全局 8：`base.css` 的 `scaleIn` / `cleaning-pulse` / `glowPulse` /',
    '`slideDown` / `pulse` / `float` / `gradientShift` 与 `graph.css` 的 `graph-spin`；',
    '模块内 1：`CleaningPanel.module.css` 的 `cleaningPulse`）',
    '+ **1 个令牌**（`--navbar-height`）。模块里的 `graphSpin` / `authScaleIn` / `feedbackScaleIn` /',
    '`qaSlideUp` / `uploadGlowPulse` / `sidebarOverlayFadeIn` / `feedbackShake` **都有引用者，不动**。',
    '',
    '> 记账口径说明：任务清单里写的是"`base.css` 4 个没有用户的 `@keyframes`"。',
    '> 实测把"没有用户"分成了两类：**迁移孤儿** 3 个（`scaleIn` / `cleaning-pulse` / `glowPulse`，',
    '> 计划 §7.0 第 2、5 条各自点过名）与**从来没有用户** 4 个（`slideDown` / `pulse` / `float` /',
    '> `gradientShift`）。任务里的"4"正好是后者；本轮把 7 个一起删了 —— 两类都是零引用，',
    '> 而且 `verify-built-css.mjs` 新增的"定义了但没人引用"检查（第 7 项）现在要求',
    '> **产物里每个 `@keyframes` 都有引用者**，只删一半会让那条检查永远红灯。',
    '',
    '## 证据一：`src/**` 全量扫描（一次性脚本的真实输出，用完已删）',
    '',
    '扫描范围：`src/**/*.{ts,tsx,css}` 的每一行；另有一种专门找"运行时拼出来的类名"的',
    '模式（`classList.add/remove/toggle/contains`、模板字符串、`styles[...]`）。',
    '',
    '```',
    '=== TOKEN navbar —— src 下 13 处 ===',
    '   src/components/Sidebar.tsx:4: * 视觉重构：替代原有顶部 Navbar        ← 注释，不是引用',
    '   src/styles/base.css:100:   --navbar-height: 64px;',
    '   src/styles/layout.css:31:  .navbar {',
    '   src/styles/layout.css:49:  .navbar-logo {',
    '   src/styles/layout.css:63:  .navbar-logo:hover {',
    '   src/styles/layout.css:67:  .navbar-links {',
    '   src/styles/layout.css:73:  .navbar-logout {',
    '   src/styles/layout.css:79:  .navbar-logout:hover {',
    '   （其余 5 处是 layout.css 文件头注释里解释"为什么还留着"的文字）',
    '',
    '=== TOKEN link-modal —— src 下 3 处 ===',
    '   src/pages/notedetail/LinkManagerModal.tsx:10: * 5 个预留语义化类（`.link-modal` / …   ← 注释',
    '   src/styles/components.css:361: .link-modal {',
    '   src/styles/refinements.css:25: （文件头注释里的去向表）',
    '',
    '=== TOKEN material-list-item —— src 下 3 处 ===（同样只有注释 + 定义）',
    '=== TOKEN type-badge —— src 下 3 处 ===（同样只有注释 + 定义）',
    '=== TOKEN cleaningProgress —— src 下 5 处 ===',
    '   src/components/CleaningPanel.module.css:56: .cleaningProgress {',
    '   src/components/CleaningPanel.module.css:64: .cleaningProgressBar {',
    '   （其余 3 处是"它没有用户"的记账注释）',
    '',
    '=== 动态类名写入点（classList / 模板字符串 / styles[） ===',
    '   共 60 余处（`styles.sidebarOpenLock` / `` `${styles.quizOption}…` `` /',
    '   `` `filter-pill${…}` `` …），**没有任何一处**与上面这些名字有关；',
    '   `useAdhdReader.ts` 的 6 处 `classList.*` 是 `.adhd-*`（另一件事，见证据 5.6-07）。',
    '```',
    '',
    '## 证据二：产物里的动画名（CSS Modules 会把动画名一起哈希，所以必须看产物）',
    '',
    '```',
    '产物 @keyframes 定义（迁移 + 清理之后）：',
    '   index-*.css: fadeIn, slideUp, shimmer, spin, shake, citation-flash,',
    '                _sidebarOverlayFadeIn_*, _authScaleIn_*',
    '   KnowledgeGraph-*.css: _graphSpin_*      QA-*.css: _qaSlideUp_*',
    '   Upload-*.css: _uploadGlowPulse_*        useSelfRating-*.css: _feedbackScaleIn_*, _feedbackShake_*',
    '   NoteDetail-*.css: （清理后已无动画）',
    '',
    '删除前逐个动画名在产物里出现的位置（每个都只出现 1 次 = 只有定义、没有引用）：',
    '   graph-spin: index-*.css×1        scaleIn: index-*.css×1',
    '   cleaning-pulse: index-*.css×1    glowPulse: index-*.css×1',
    '   slideDown: index-*.css×1         gradientShift: index-*.css×1',
    '```',
    '',
    '## 证据三：机器自检（`verify-built-css.mjs`，三个方向）',
    '',
    '每个条目都要过：**a. 迁移前那个文件里确实有它**（从 HEAD 往回按内容定位修订 ——',
    '写死 `HEAD` 会在删除提交之后失效）→ **b. 源码里没有了**（剥注释后比较，',
    '否则墓碑注释本身会把自检弄红）→ **c. 产物里没有了**。',
    '输出见 `5.6-03-built-css.md` 的"收尾轮死代码清理"一节（实测 21/21 通过）；',
    '同一份输出里还有新增的"产物里定义了但没人引用的 `@keyframes`"检查 ——',
    '它让这一轮删掉的动画**不可能悄悄回来**（唯一的合法例外 `shake` 是"只被行内样式引用"，',
    '点名登记在 `INLINE_ONLY_KEYFRAMES` 里）。',
    '',
    '## 差集侧：这些删除怎么记账的',
    '',
    '`css-migration-diff.mjs` 的批次汇总里，第一批从 **60 逐字保留** 变成',
    '**58 逐字保留 + 2 已声明删除**（那两个 `.cleaning-progress*` 就是本条表格里的一组），',
    '第八批从 13 条"从未生效"的删除变成 **19 条已声明删除**（多了 5 个预留类的 6 条规则）。',
    '两处都不是"少了东西"，而是"删掉的东西逐条登记过"。',
    '',
    '## 与"不删"原则的关系（这条写入规范）',
    '',
    '前几批**刻意不删**任何一条（契约是"证明什么都没丢"），所以死代码攒到了这一轮。',
    '这一轮之后的分工是：',
    '',
    '- 迁移相关的工作仍然"逐字搬、不删"，用差集证明"丢失 0"；',
    '- **删除**必须单独一轮、单独成节（`resolvedConflicts` 的 `note` 字段 + `CLEANUP_RETIREMENTS` 表），',
    '  并且带上"谁在用它"的反向证据；',
    '- 删掉的类名/动画名**不许悄悄回来**：`CLEANUP_RETIREMENTS` 与',
    '  "产物里没有死动画"两项检查一起兜住。',
  ].join('\n'),
)

// ── 10. 差集（核心证据，含所有批次） ──
const diff = run('css-migration-diff.mjs')
write(
  '5.6-02-rule-diff.md',
  '5.6 规则清单差集：迁移前(git 源码) vs 迁移后(dist 产物)',
  '两侧都解析成「上下文 + 选择器 + 声明」，类名归一为 `.C0`… 占位符后逐条比。\n' +
    '`animation` 的值单独由「动画绑定」与「动画体比对」两项负责（哈希化的动画名无法逐字比）。\n' +
    '「声明过的选择器强化」一节现在是空的：第一批曾用 `:global(.btn).authSubmit` 顶住\n' +
    '级联反转，后来改成**修正 `main.tsx` 的导入顺序**（全局样式表在组件之前，\n' +
    '产物顺序 = ①令牌 → ②全局 → ③模块）并把选择器还原成单类，\n' +
    '所以不再有任何选择器文本变化。\n\n' +
    `运行退出码：${diff.code}（0 = 没有丢失、动画绑定自洽、动画体一致）\n\n\`\`\`\n${diff.out.trim()}\n\`\`\``,
)

// ── 5. 产物校验（含所有批次） ──
const verify = run('verify-built-css.mjs')
write(
  '5.6-03-built-css.md',
  '5.6 产物 CSS 校验：动画绑定 / 冲突归因 / 级联次序 / 切片与退休类名',
  '检查 dist 产物而不是源码：CSS Modules 会把类名**和 @keyframes 动画名**一起哈希，\n' +
    '源码里"规则还在"不等于浏览器里还生效（试点轮就是这么抓到动画悬空的）。\n' +
    '「级联次序」一项在第一批加入、第二批靠它验证了 `main.tsx` 重排的效果：\n' +
    '`.authSubmit` 现在**靠源序**取胜（权重相同、同文件后者胜），不再依赖提权。\n' +
    '反向验证过：把 `main.tsx` 的顺序改回去，这项立刻报 4 个属性得主是 global。\n\n' +
    `运行退出码：${verify.code}（0 = 无悬空动画、改动范围内无冲突、级联得主正确、退休类名已消失）\n\n\`\`\`\n${verify.out.trim()}\n\`\`\``,
)

console.log('已生成：')
for (const f of files) console.log(`  docs/migration-evidence/${f.name}  (${f.bytes} B)`)
console.log('\n退出码：差集=' + diff.code + ' 产物校验=' + verify.code)
if (auditFailed) {
  console.error(
    '✗ 去向审计里有"既找不到也没有例外"的规则 —— 那是**静默丢失**，' +
      '要么把它找回来，要么在 EMPTY_SHEET_EXCEPTIONS 里写明依据。',
  )
}
if (diff.code !== 0 || verify.code !== 0 || auditFailed) process.exit(1)
