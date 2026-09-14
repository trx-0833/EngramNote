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
 *   docs/migration-evidence/5.6-01-before-learning-css.md   试点：迁移前规则清单
 *   docs/migration-evidence/5.6-01-before-responsive-css.md 试点：迁移前响应式规则清单
 *   docs/migration-evidence/5.6-02-rule-diff.md             迁移前后差集（**含所有批次**）
 *   docs/migration-evidence/5.6-03-built-css.md             产物 CSS 校验（**含所有批次**）
 *   docs/migration-evidence/5.6-04-before-batch1.md         第一批：迁移前规则清单
 *
 * ⚠️ 修订（rev）要按批次给对：
 *   - 试点（quiz 切片）**已经提交**在 `6acb21c`，它的"迁移前"是 `HEAD~1`；
 *   - 第一批尚未提交，"迁移前"就是 `HEAD`。
 * 给错的直接表现是"清单 0 条"，脚本会报错退出而不是写出一份空证据。
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

/** 跑一次清单并把结果写成一个证据文件 */
function inventoryEvidence({ name, title, sheet, classes, rev }) {
  const r = run('css-rule-inventory.mjs', [
    sheet,
    '--class',
    classes.join(','),
    '--from-git',
    ...(rev ? ['--rev', rev] : []),
  ])
  if (r.code !== 0) throw new Error(`清单生成失败（${sheet}）：${r.out}`)
  write(name, title, `取自 \`git show ${rev || 'HEAD'}:frontend/${sheet}\`。\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}

// ── 1. 试点（quiz 切片）迁移前清单：已提交，故从 HEAD~1 取 ──
inventoryEvidence({
  name: '5.6-01-before-learning-css.md',
  title: '5.6 迁移前：learning.css 中属于答题卡片切片的规则',
  sheet: 'src/styles/learning.css',
  classes: ['quiz-option', 'quiz-option-selected', 'feedback-correct', 'feedback-incorrect'],
  rev: 'HEAD~1',
})
inventoryEvidence({
  name: '5.6-01-before-responsive-css.md',
  title: '5.6 迁移前：responsive.css 中 .self-rating-btn 的规则',
  sheet: 'src/styles/responsive.css',
  classes: ['self-rating-btn'],
  rev: 'HEAD~1',
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
  const r = run('css-rule-inventory.mjs', [
    s.sheet,
    '--class',
    s.classes.join(','),
    '--from-git',
  ])
  if (r.code !== 0) throw new Error(`清单生成失败（${s.sheet}）：${r.out}`)
  parts.push(`## ${s.sheet} @HEAD\n\n\`\`\`\n${r.out.trim()}\n\`\`\``)
}
write(
  '5.6-04-before-batch1.md',
  '5.6 迁移前：第一批（auth / cleaning / diff / dashboard 私有部分）的规则清单',
  '五个样式表各自只列出**本批搬走的那些类名**对应的规则（留在全局的\n' +
    '`.stat-card*` / `.stat-label` / `.progress-bar*` 不在清单里，它们没有搬）。\n' +
    '取自 `git show HEAD:frontend/...`（第一批尚未提交）。\n\n' +
    parts.join('\n\n'),
)

// ── 3. 差集（核心证据，含所有批次） ──
const diff = run('css-migration-diff.mjs')
write(
  '5.6-02-rule-diff.md',
  '5.6 规则清单差集：迁移前(git 源码) vs 迁移后(dist 产物)',
  '两侧都解析成「上下文 + 选择器 + 声明」，类名归一为 `.C0`… 占位符后逐条比。\n' +
    '`animation` 的值单独由「动画绑定」与「动画体比对」两项负责（哈希化的动画名无法逐字比）。\n' +
    '另有「声明过的选择器强化」一节：模块 CSS 在产物里排在全局样式表**之前**，\n' +
    '`.authSubmit` 必须显式提高权重（`:global(.btn).authSubmit`）才不会被 `.btn` 反盖，\n' +
    '这一处差异是声明过的、且不改变任何属性值。\n\n' +
    `运行退出码：${diff.code}（0 = 没有丢失、动画绑定自洽、动画体一致）\n\n\`\`\`\n${diff.out.trim()}\n\`\`\``,
)

// ── 4. 产物校验（含所有批次） ──
const verify = run('verify-built-css.mjs')
write(
  '5.6-03-built-css.md',
  '5.6 产物 CSS 校验：动画绑定 / 冲突归因 / 级联次序 / 切片与退休类名',
  '检查 dist 产物而不是源码：CSS Modules 会把类名**和 @keyframes 动画名**一起哈希，\n' +
    '源码里"规则还在"不等于浏览器里还生效（试点轮就是这么抓到动画悬空的）。\n' +
    '第一批新增「级联次序」一项：模块 CSS 与全局样式表同权重竞争时，得主必须没变。\n\n' +
    `运行退出码：${verify.code}（0 = 无悬空动画、改动范围内无冲突、级联得主正确、退休类名已消失）\n\n\`\`\`\n${verify.out.trim()}\n\`\`\``,
)

console.log('已生成：')
for (const f of files) console.log(`  docs/migration-evidence/${f.name}  (${f.bytes} B)`)
console.log('\n退出码：差集=' + diff.code + ' 产物校验=' + verify.code)
if (diff.code !== 0 || verify.code !== 0) process.exit(1)
