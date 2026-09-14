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

// ── 6. 差集（核心证据，含所有批次） ──
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
if (diff.code !== 0 || verify.code !== 0) process.exit(1)
