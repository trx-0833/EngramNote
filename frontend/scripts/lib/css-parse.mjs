/**
 * 极简 CSS 规则解析器（overhaul-plan 5.6 的三个证据脚本共用）
 *
 * ## 为什么要单独抽一个模块
 *
 * 本轮先后在三个脚本里各写了一遍"花括号配对 + @media 上下文"的解析，
 * 结果其中两遍各自带着一个 bug、并因此给出**错误的结论**：
 *   - `css-rule-inventory` 的 `normalize()` 把 `:hover` 改成了 `: hover`
 *     （伪类冒号被当成声明的冒号补了空格），产出的是无效选择器；
 *   - `css-migration-diff` 的递归 `walk()` 忘了把累加数组传下去，
 *     **@media 里的规则被静默丢弃** —— 而本轮要搬的两条响应式规则恰好
 *     都在 @media 里，于是"一条没丢"被误报成"7 条全丢"。
 * 解析代码重复三遍就是三个各自出错的机会。这里只留一份，
 * 三个脚本都从这里 import。
 *
 * ## 解析范围（够用就好，不追求完整 CSS 语法）
 *
 * - 去掉注释；
 * - 按花括号配对切出规则，保留 `@media` / `@supports` 上下文链；
 * - 其它 at-rule（`@keyframes` / `@font-face`）整体当作一条记录；
 * - 每个选择器组拆开、每段声明拆成 `[属性, 值]`，都**保留原始名**
 *   （要不要哈希化、要不要归一化由调用方决定）。
 *
 * 明确不做：嵌套 CSS、`@import`、字符串里的花括号。
 * 本项目的样式表不用这些；真用上了这里会解析错，所以调用方都带自检
 * （比如"见到 0 条规则"时应当报错而不是当作"没有差异"）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

/** 去掉注释（注释里的示例写法不该被当成真规则） */
export function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/**
 * 把样式表拆成规则列表。
 * @returns {{ context: string, selector: string, decls: [string, string][] }[]}
 *   context 形如 `@media (max-width: 768px)`，顶层规则为空串。
 *
 * ## 算法
 *
 * 逐字符扫，维护两个状态：
 *   - `stack`：当前所处的 at-rule 前导链（`@media` 嵌套）；
 *   - `buf`：自上一个 `{` / `}` 以来累积的文本，即"下一个块的前导"。
 * 遇到 `{` 时，`buf` 就是这条规则（或 at-rule）的前导，括号配对找到块尾，
 * 递归处理块内；遇到 `}` 时弹出一层 at-rule。
 *
 * ## 这一版之前的两个 bug（都给出过错误结论，别退回去）
 *
 * 1. **`looksLikeMedia()` 猜测**（"前导以 `@` / `)` / 空串开头就算
 *    媒体查询的尾巴，要把栈顶拼回去"）：普通选择器也被误判，
 *    `@media (…) { .sel { … } }` 里的 `.sel` 被拼成 `@media (…).sel`，
 *    再因前导以 `@` 开头而被当成 at-rule 丢掉 ——
 *    现象是"媒体查询里的规则一条都解析不出来"。
 * 2. **`pendingParens` 续行判断**：想用"前导里还有没闭合的 `(`"判断
 *    `@media` 前导是否被切断。但 `@media (max-width: 768px)` 的括号是
 *    **成对**的（净深度 0），于是内层规则的 `{` 被当成"续行"、
 *    内容被拼进栈顶后丢弃 —— 媒体查询里的规则**照样全部消失**。
 *
 * 两次都是"想聪明地拼接跨片段的前导"。实际上 CSS 的块前导在遇到 `{`
 * 之前不可能被别的 `{` 打断，所以**不需要任何拼接**：`buf` 到 `{` 时
 * 天然就是完整前导。删掉拼接逻辑后就没有出错的地方了。
 */
export function parseRules(css) {
  const src = stripComments(css)
  const out = []
  const stack = []
  let buf = ''
  let i = 0

  while (i < src.length) {
    const ch = src[i]

    if (ch === '{') {
      // 括号配对找块尾（CSS 块不会嵌套字符串里的花括号，本项目样式表也没有）
      let depth = 1
      let j = i + 1
      while (j < src.length && depth > 0) {
        if (src[j] === '{') depth++
        else if (src[j] === '}') depth--
        j++
      }
      const inner = src.slice(i + 1, j - 1)
      const head = buf.replace(/\s+/g, ' ').trim()
      buf = ''

      if (head.startsWith('@')) {
        if (/^@(media|supports|layer|container)\b/.test(head)) {
          // 条件块：把前导入栈，块内继续按规则解析
          stack.push(head)
          parseInto(inner, [...stack], out)
          stack.pop()
        } else {
          // @keyframes / @font-face 之类：整体当作一条记录
          out.push({ context: stack.join(' '), selector: head, decls: parseDecls(inner) })
        }
      } else if (head) {
        out.push({ context: stack.join(' '), selector: head, decls: parseDecls(inner) })
      }

      i = j
      continue
    }

    if (ch === '}') {
      stack.pop()
      buf = ''
      i++
      continue
    }

    buf += ch
    i++
  }
  return out
}

/**
 * 解析一段块内容（`@media` 内部），把规则追加到 out。
 * 与 parseRules 同构，但 context 由调用方给定 —— 拆成两个函数是为了让
 * "条件块递归"这条路只有一处实现，不必再维护跨片段拼接。
 */
function parseInto(src, context, out) {
  let buf = ''
  let i = 0
  while (i < src.length) {
    const ch = src[i]
    if (ch === '{') {
      let depth = 1
      let j = i + 1
      while (j < src.length && depth > 0) {
        if (src[j] === '{') depth++
        else if (src[j] === '}') depth--
        j++
      }
      const inner = src.slice(i + 1, j - 1)
      const head = buf.replace(/\s+/g, ' ').trim()
      buf = ''
      if (head.startsWith('@')) {
        if (/^@(media|supports|layer|container)\b/.test(head)) {
          parseInto(inner, [...context, head], out)
        } else {
          out.push({ context: context.join(' '), selector: head, decls: parseDecls(inner) })
        }
      } else if (head) {
        out.push({ context: context.join(' '), selector: head, decls: parseDecls(inner) })
      }
      i = j
      continue
    }
    if (ch === '}') {
      buf = ''
      i++
      continue
    }
    buf += ch
    i++
  }
}

/** 声明块 → [属性, 值] 列表（保留原始大小写与空白差异，由调用方归一化） */
export function parseDecls(body) {
  const out = []
  for (const part of body.split(';')) {
    const idx = part.indexOf(':')
    if (idx < 0) continue
    const prop = part.slice(0, idx).trim()
    const val = part.slice(idx + 1).trim()
    if (prop) out.push([prop, val])
  }
  return out
}

/**
 * 按**顶层**逗号拆选择器组。
 * 不能直接 split(',')：`[style*="rgba(0,0,0,0.5)"]` 里的逗号会被切断，
 * 造出 `[style*="rgba(0` 这种假选择器（本项目 refinements.css 真有这种写法）。
 */
export function splitSelectors(selector) {
  const out = []
  let depth = 0
  let cur = ''
  for (const ch of selector) {
    if (ch === '(' || ch === '[') depth++
    else if (ch === ')' || ch === ']') depth--
    if (ch === ',' && depth === 0) {
      out.push(cur.trim())
      cur = ''
    } else {
      cur += ch
    }
  }
  if (cur.trim()) out.push(cur.trim())
  return out
}

/** 从选择器里取所有类名 */
export function classesOf(selector) {
  return [...selector.matchAll(/\.([a-zA-Z_][\w-]*)/g)].map((m) => m[1])
}

/**
 * 从某个 git 修订里读文件（迁移一旦落地，工作区里就没有旧版本了）。
 * 往上找 `.git` 定位仓库根，而不是写死目录层级 —— 写死会在挪目录时静默读错。
 */
export function readFromGit(absFile, rev = 'HEAD') {
  let dir = path.resolve(path.dirname(absFile))
  while (!fs.existsSync(path.join(dir, '.git'))) {
    const parent = path.dirname(dir)
    if (parent === dir) throw new Error(`往上找不到 .git（起点 ${absFile}）`)
    dir = parent
  }
  const rel = path.relative(dir, path.resolve(absFile)).replace(/\\/g, '/')
  return execFileSync('git', ['show', `${rev}:${rel}`], {
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024,
  })
}

/**
 * 从 HEAD 往回找**最近一个**满足 `matches(rev)` 的修订。
 *
 * ## 为什么需要它（第二批实测踩到）
 *
 * "迁移前"的修订原来写成 `HEAD~1` / `HEAD~2` 这种相对计数，理由是
 * "试点提交在 HEAD~1、第一批提交在 HEAD~2"。**这个前提会被无关提交打破**：
 * 本项目是多个 agent 并行改同一个仓库，第二批期间另一个 agent 提交了一个
 * 后端改动，HEAD 因此往前挪了一位，所有 `HEAD~N` 全部指错 ——
 * 表现是"迁移前 0 条"。脚本会报错退出（不会静默给出错误结论），
 * 但每来一个无关提交都要人工重算一遍，这不是能长期维持的做法。
 *
 * 改成**按内容定位**：从 HEAD 往回走，第一个"还含有这批老类名"的修订
 * 就是迁移前。它与提交顺序、与期间有多少无关提交都无关。
 *
 * 代价是每个候选修订多一次 `git show`；`maxDepth` 40 对本地仓库是毫秒级。
 * 真超出深度会返回 null，让调用方报错 —— 绝不静默取一个错的修订。
 */
export function findRecentRev(matches, { maxDepth = 40 } = {}) {
  for (let i = 0; i < maxDepth; i += 1) {
    const rev = i === 0 ? 'HEAD' : `HEAD~${i}`
    let ok = false
    try {
      ok = matches(rev)
    } catch {
      // 该修订里文件还不存在（新增文件）→ 不满足条件，继续往前找
      ok = false
    }
    if (ok) return rev
  }
  return null
}
