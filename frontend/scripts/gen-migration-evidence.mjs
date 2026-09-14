/**
 * 生成 5.6 迁移证据文件（overhaul-plan 5.6）
 *
 * ## 为什么由 Node 写文件，而不是在 shell 里重定向
 *
 * 本项目已经两次被 PowerShell 写坏 UTF-8，而证据文件里全是中文注释。
 * 实测本机 `Out-File -Encoding utf8` 会加 BOM（EF BB BF），
 * `*>` 重定向更会写成 UTF-16LE（FF FE）—— 两种都不是本项目要的编码。
 * `fs.writeFileSync(..., 'utf8')` 写出来是无 BOM 的 UTF-8，且只有一个实现。
 *
 * ## 生成什么
 *
 *   docs/migration-evidence/5.6-01-before-*.md    迁移前（HEAD）的规则清单
 *   docs/migration-evidence/5.6-02-rule-diff.md   迁移前后差集（核心证据）
 *   docs/migration-evidence/5.6-03-built-css.md   产物 CSS 校验（动画/冲突/退休类名）
 *
 * 用法：node scripts/gen-migration-evidence.mjs
 *   前置：已跑过 npm run build（差值取自 dist 产物）
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

// ── 1. 迁移前清单（从 HEAD 取，工作区里已经没有了） ──
const beforeLearning = run('css-rule-inventory.mjs', [
  'src/styles/learning.css',
  '--class',
  'quiz-option,quiz-option-selected,feedback-correct,feedback-incorrect',
  '--from-git',
])
write(
  '5.6-01-before-learning-css.md',
  '5.6 迁移前：learning.css 中属于答题卡片切片的规则',
  '取自 `git show HEAD:frontend/src/styles/learning.css`（迁移已落地，工作区里没有旧版本了）。\n\n```\n' +
    beforeLearning.out.trim() +
    '\n```',
)

const beforeResponsive = run('css-rule-inventory.mjs', [
  'src/styles/responsive.css',
  '--class',
  'self-rating-btn',
  '--from-git',
])
write(
  '5.6-01-before-responsive-css.md',
  '5.6 迁移前：responsive.css 中 .self-rating-btn 的规则',
  '取自 `git show HEAD:frontend/src/styles/responsive.css`。\n\n```\n' +
    beforeResponsive.out.trim() +
    '\n```',
)

// ── 2. 差集（核心证据） ──
const diff = run('css-migration-diff.mjs')
write(
  '5.6-02-rule-diff.md',
  '5.6 规则清单差集：迁移前(HEAD 源码) vs 迁移后(dist 产物)',
  '两侧都解析成「上下文 + 选择器 + 声明」，类名归一为 `.C0`…`.C4` 占位符后逐条比。\n' +
    '`animation` 的值单独由「动画绑定」与「动画体比对」两项负责（哈希化的动画名无法逐字比）。\n\n' +
    `运行退出码：${diff.code}（0 = 没有丢失、动画绑定自洽、动画体一致）\n\n\`\`\`\n${diff.out.trim()}\n\`\`\``,
)

// ── 3. 产物校验 ──
const verify = run('verify-built-css.mjs')
write(
  '5.6-03-built-css.md',
  '5.6 产物 CSS 校验：动画绑定 / 冲突归因 / 退休类名',
  '检查 dist 产物而不是源码：CSS Modules 会把类名**和 @keyframes 动画名**一起哈希，\n' +
    '源码里"规则还在"不等于浏览器里还生效（本轮就是这么抓到动画悬空的）。\n\n' +
    `运行退出码：${verify.code}（0 = 无悬空动画、改动范围内无冲突、退休类名已消失）\n\n\`\`\`\n${verify.out.trim()}\n\`\`\``,
)

console.log('已生成：')
for (const f of files) console.log(`  docs/migration-evidence/${f.name}  (${f.bytes} B)`)
console.log('\n退出码：差集=' + diff.code + ' 产物校验=' + verify.code)
if (diff.code !== 0 || verify.code !== 0) process.exit(1)
