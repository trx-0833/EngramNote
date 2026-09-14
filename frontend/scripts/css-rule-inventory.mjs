/**
 * CSS 规则清单工具（overhaul-plan 5.6 CSS Modules 迁移的证据工具）
 *
 * ## 为什么需要它
 *
 * "迁移后样式没丢"这句话不能靠肉眼比对两份样式表来证明 —— 14 个样式表、
 * 上千条声明，漏掉一条（比如某个 `:hover` 或 `::placeholder`）在界面上
 * 往往看不出来，直到用户碰到那个状态。所以迁移前后各生成一份**规则清单**，
 * 机械地做差集：剩下的每一条都要能说清"它去哪了"。
 *
 * 解析统一走 `lib/css-parse.mjs`（本轮三个脚本各写一遍解析器，
 * 各自带 bug 并给出过错误结论，见那个文件的文件头）。
 *
 * ## 用法
 *
 *   # 迁移前（工作区里已经没有旧规则了，所以要回到 git 修订里取）
 *   node scripts/css-rule-inventory.mjs src/styles/learning.css \
 *        --class quiz-option,feedback-correct --from-git
 *
 *   # 指定修订：已经提交过的批次，它的"迁移前"在 HEAD 之前
 *   node scripts/css-rule-inventory.mjs src/styles/learning.css \
 *        --class quiz-option --from-git --rev HEAD~1
 *
 *   # 按选择器前缀过滤
 *   node scripts/css-rule-inventory.mjs src/styles/learning.css --prefix quiz-,feedback-
 *
 *   # 全部规则
 *   node scripts/css-rule-inventory.mjs src/styles/learning.css
 */
import fs from 'node:fs'
import path from 'node:path'
import { parseRules, classesOf, readFromGit, findRecentRev } from './lib/css-parse.mjs'

const argv = process.argv.slice(2)

// ── 归一化 ──
// 目的：让"只改了排版"不被当成规则变化。
// ⚠️ 只给**声明**的冒号补空格。第一版用 `/ \*:\s*/g` 全局替换，
// 把 `.quiz-option:hover` 改成了 `.quiz-option: hover`（无效选择器），
// 于是清单里躺着一条根本不存在的规则。伪类的冒号后面不跟空格，
// 所以用 `(?=\s)` 把它排除掉。
function normalize(s) {
  return s
    .replace(/\s+/g, ' ')
    .replace(/(?<=[\w)]) ?: ?(?=\s)/g, ': ')
    .replace(/\s*;\s*/g, '; ')
    .replace(/;\s*$/, '')
    .replace(/,\s*/g, ', ')
    .replace(/\s+/g, ' ')
    .trim()
}

function formatRule(r) {
  const decls = r.decls.map(([p, v]) => `${p}: ${normalize(v)}`).join('; ')
  return `${r.context ? r.context + ' ' : ''}${normalize(r.selector)} { ${decls} }`
}

const file = argv[0]
if (!file) {
  console.error(
    '用法: node scripts/css-rule-inventory.mjs <css文件> [--prefix a,b] [--class a,b] [--from-git] [--rev R]',
  )
  process.exit(2)
}

const prefixArg = argv.indexOf('--prefix')
const prefixes =
  prefixArg >= 0 && argv[prefixArg + 1] ? argv[prefixArg + 1].split(',').filter(Boolean) : null
const classArg = argv.indexOf('--class')
const exactClasses =
  classArg >= 0 && argv[classArg + 1] ? new Set(argv[classArg + 1].split(',').filter(Boolean)) : null
const fromGit = argv.includes('--from-git')
// `--rev` 默认**按内容自动定位**：从 HEAD 往回找第一个还含有 `--class` 里那些
// 类名的修订，那个才是"迁移前"。写死 `HEAD~N` 会被并行的无关提交打乱 ——
// 第二批期间另一个 agent 提交了一个后端改动，所有相对计数就集体错位了。
// 没给 `--class` 时无从判断，退回 HEAD。
const revArg = argv.indexOf('--rev')
const explicitRev = revArg >= 0 && argv[revArg + 1] ? argv[revArg + 1] : null

let rev = explicitRev
if (fromGit && !rev) {
  if (!exactClasses || exactClasses.size === 0) {
    rev = 'HEAD'
  } else {
    const abs = path.resolve(file)
    rev = findRecentRev((r) =>
      parseRules(readFromGit(abs, r)).some((rule) =>
        classesOf(rule.selector).some((c) => exactClasses.has(c)),
      ),
    )
    if (!rev) {
      console.error(
        `✗ 从 HEAD 往回找不到含有 ${[...exactClasses].join('|')} 的修订：` +
          `类名写错了，或者这些规则从来没在那个文件里 —— 两种情况都不该继续。`,
      )
      process.exit(3)
    }
  }
}

const css = fromGit ? readFromGit(path.resolve(file), rev) : fs.readFileSync(file, 'utf8')
let rules = parseRules(css)
// 自检：解析出 0 条规则几乎一定是解析器或路径/修订出了问题，
// 而不是"这个文件真的没有规则"。静默返回空清单会让差集看起来"没丢东西"。
if (rules.length === 0) {
  console.error(`✗ 从 ${file} 解析出 0 条规则：检查路径/解析器/修订，不要把它当成"文件是空的"`)
  process.exit(3)
}
if (prefixes) {
  rules = rules.filter((r) => prefixes.some((p) => classesOf(r.selector).some((c) => c.startsWith(p))))
}
if (exactClasses) {
  rules = rules.filter((r) => classesOf(r.selector).some((c) => exactClasses.has(c)))
}

console.log(
  `# ${path.basename(file)}${fromGit ? ` @${rev}` : ''}` +
    `${prefixes ? ` prefix=${prefixes.join('|')}` : ''}` +
    `${exactClasses ? ` classes=${[...exactClasses].join('|')}` : ''} —— ${rules.length} 条规则`,
)
for (const r of rules) console.log(formatRule(r))
