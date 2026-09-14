/**
 * @file iOS 聚焦缩放（focus zoom）：两个输入框的字号护栏
 *
 * ## 为什么需要这个文件
 *
 * iOS Safari 在聚焦 `font-size` **小于 16px** 的输入框（input / textarea）时
 * 会把整页放大。这条规则有两个"看着修好了、其实没修好"的经典坑，
 * 本文件就是来钉住它们的：
 *
 * 1. **改错文件**：`main.tsx` 的导入顺序是 responsive.css(12) →
 *    markdown-extras.css(13)。把 `.ask-ai-input` 的窄屏兜底写进 responsive.css，
 *    会被 markdown-extras.css 里的同权重规则压掉（级联：同权重看先后），
 *    规则"存在"但永不生效。所以这里不查"某条规则在不在"，
 *    而是按**真实导入顺序**把样式表拼起来，让 jsdom 这个真实 CSS 引擎算出最终值。
 * 2. **改用内联 style**：行内样式永远压过样式表，那样测试会绿而问题照旧。
 *    本文件只读样式表，所以产品代码一旦改用 `style={{ fontSize }}`，
 *    "计算值"就不再来自样式表 —— 下面的"归属权"用例专门拦住这种情况。
 *
 * ## 为什么要用 fs 亲自读文件
 *
 * 本项目 vite.config.ts 没开 `test.css`（默认 false），Vitest 会把**任何**
 * `.css` 导入变成空串：`?raw` / `?inline` / glob 都实测过，拿到的都是
 * `""`（`?raw` 对 `.tsx` 有效，对 `.css` 无效）。护栏读的必须是真实样式表，
 * 所以这里从磁盘读。`node:fs` 的类型在本文件里就地声明 —— 本项目没装
 * `@types/node`，为一条护栏引入依赖不值得，而 `vi.importActual` 的泛型
 * 允许类型完全由调用方给出。
 *
 * ## jsdom 的能力边界（对照实验结论，不是猜的）
 *
 * - **认**：权重、同权重下的先后顺序（同一文件内后者胜，跨文件按拼接顺序胜）。
 * - **不认**：`@media` —— jsdom 30 里 `window.matchMedia` 根本不存在，
 *   `getComputedStyle` 完全跳过媒体查询块（对照实验：把
 *   `@media (max-width:768px){ .x{font-size:33px} }` 放在
 *   `.x{font-size:44px}` 之后，innerWidth=375 下读到的仍是 44px）。
 *   所以**窄屏那条路径**由本文件的"源码扫描"用例兜住：
 *   任何文件、任何媒体块里都不许给这两个类名写 <16px 的字号。
 *
 * ## 与真浏览器的差别（不假装覆盖）
 *
 * jsdom 不做布局：这里能证明"级联算出来是 16px"，但**不能**证明真机上
 * 不再缩放，也不能证明字号变大后工具栏/浮层没被挤坏 —— 那要靠真机或截图。
 */
import { describe, expect, it, vi } from 'vitest'

/** 只声明本文件用到的那几个 API（本项目没有 @types/node） */
interface NodeFs {
  readFileSync(path: string, encoding: 'utf8'): string
  readdirSync(path: string): string[]
}

const fs = await vi.importActual<NodeFs>('node:fs')

/** iOS 的阈值：1rem = 16px（base.css 里 `html { font-size: 16px }`） */
const IOS_MIN_FONT_PX = 16

interface Target {
  /** 输入框自身的类名 */
  className: string
  tag: 'input' | 'textarea'
  /** 拥有这个值的样式表：改动必须发生在这里 */
  owner: string
  /** 谁在用（失败信息里好定位） */
  where: string
}

const TARGETS: Target[] = [
  {
    className: 'ask-ai-input',
    tag: 'textarea',
    owner: 'markdown-extras.css',
    where: 'NoteAskPanel.tsx 的「AI 提问」输入框',
  },
  {
    className: 'graph-search-input',
    tag: 'input',
    owner: 'graph.css',
    where: 'GraphToolbar.tsx 的图谱搜索框',
  },
]

/** 本文件所在目录 = src/styles（Vitest 给出的是原生绝对路径） */
function stylesDir(): string {
  const testPath = expect.getState().testPath
  if (!testPath) throw new Error('expect.getState().testPath 为空：定位不到 src/styles')
  return testPath.replace(/[/\\][^/\\]*$/, '')
}

let sheetsCache: Map<string, string> | null = null

/** src/styles 下的全部样式表：文件名 → 原文 */
function readStylesheets(): Map<string, string> {
  if (!sheetsCache) {
    const dir = stylesDir()
    sheetsCache = new Map(
      fs
        .readdirSync(dir)
        .filter((name) => name.endsWith('.css'))
        .map((name) => [name, fs.readFileSync(`${dir}/${name}`, 'utf8')]),
    )
  }
  return sheetsCache
}

function readMainSource(): string {
  return fs.readFileSync(`${stylesDir()}/../main.tsx`, 'utf8')
}

interface FontSizeDecl {
  file: string
  /** 外层媒体查询（顶层规则为空串）—— 保留它才不会漏掉窄屏兜底 */
  media: string
  /** 规则的选择器原文（可能是逗号分隔的一组） */
  selectors: string
  value: string
}

/** 极简 CSS 扫描：只关心 font-size，但保留媒体查询上下文 */
function scanFontSizeDeclarations(): FontSizeDecl[] {
  const out: FontSizeDecl[] = []
  for (const [file, css] of readStylesheets()) {
    const text = css.replace(/\/\*[\s\S]*?\*\//g, '') // 注释里的写法示例不该被当成声明
    const stack: string[] = []
    let prelude = ''
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i]
      if (ch === '{') {
        const head = prelude.trim()
        prelude = ''
        if (head.startsWith('@')) {
          // @media / @keyframes / @supports …：记住上下文，块内的规则照常扫
          stack.push(head)
          continue
        }
        const end = text.indexOf('}', i)
        if (end === -1) break // 括号不配对：宁可少扫也不要死循环
        const body = text.slice(i + 1, end)
        for (const m of body.matchAll(/font-size\s*:\s*([^;]+)/gi)) {
          out.push({ file, media: stack.join(' '), selectors: head, value: m[1].trim() })
        }
        i = end
        continue
      }
      if (ch === '}') {
        stack.pop()
        prelude = ''
        continue
      }
      prelude += ch
    }
  }
  return out
}

/** 一条规则的选择器组里，命中了哪些目标输入框（`::placeholder` 不改变元素自身字号） */
function targetsIn(selectorText: string): Target[] {
  if (selectorText.includes('::')) return []
  return TARGETS.filter((t) => new RegExp(`\\.${t.className}(?![\\w-])`).test(selectorText))
}

/** rem/px → px；看不懂的写法返回 null（由调用方判失败，绝不静默放过） */
function toPx(value: string, rootPx: number): number | null {
  const v = value.replace(/!important/gi, '').trim().toLowerCase()
  const m = /^([\d.]+)(px|rem)$/.exec(v)
  if (!m) return null
  const n = Number.parseFloat(m[1])
  return m[2] === 'px' ? n : n * rootPx
}

/** 级联顺序 = main.tsx 里的 import 顺序（本项目没有 @import，样式表都在那里引入） */
function stylesheetImportOrder(): string[] {
  const files = [...readMainSource().matchAll(/import\s+'\.\/styles\/([^']+\.css)'/g)].map((m) => m[1])
  expect(files.length, 'main.tsx 里解析不到 CSS 导入：导入写法变了，请同步本文件的正则').toBeGreaterThan(0)
  const known = [...readStylesheets().keys()]
  expect(
    known.filter((f) => !files.includes(f)),
    'src/styles 下有样式表没被 main.tsx 引入（或导入写法没被解析到）：护栏会漏掉它',
  ).toEqual([])
  return files
}

function withStyles<T>(fn: () => T): T {
  const sheets = readStylesheets()
  const style = document.createElement('style')
  style.textContent = stylesheetImportOrder()
    .map((file) => sheets.get(file))
    .join('\n')
  document.head.appendChild(style)
  try {
    return fn()
  } finally {
    style.remove()
  }
}

/** 把元素挂进文档、注入全部样式表，读 jsdom 算出的 font-size（真实级联：权重 + 先后） */
function computedFontSizePx(tag: Target['tag'], className: string): number {
  return withStyles(() => {
    const el = document.createElement(tag)
    el.className = className
    document.body.appendChild(el)
    try {
      return Number.parseFloat(window.getComputedStyle(el).fontSize)
    } finally {
      el.remove()
    }
  })
}

describe('iOS 聚焦缩放：输入框的计算字号不得低于 16px', () => {
  it('★ 按 main.tsx 的真实导入顺序级联后，两个输入框都 ≥16px（jsdom 引擎判读）', () => {
    // 顺序前提：responsive.css 在前、markdown-extras.css 在后。
    // 正因如此，把 .ask-ai-input 的兜底写进 responsive.css 会输给后者（同权重看先后）。
    const files = stylesheetImportOrder()
    expect(files.indexOf('markdown-extras.css')).toBeGreaterThan(files.indexOf('responsive.css'))

    for (const t of TARGETS) {
      const px = computedFontSizePx(t.tag, t.className)
      expect(
        px,
        `${t.className}（${t.where}）的计算字号是 ${px}px < ${IOS_MIN_FONT_PX}px：` +
          `iOS Safari 聚焦它会放大整页。请在 ${t.owner} 里把值抬到 1rem。`,
      ).toBeGreaterThanOrEqual(IOS_MIN_FONT_PX)
    }
  })

  it('★ 任何样式表、任何媒体块里都不许给这两个输入框写 <16px 的字号', () => {
    // jsdom 不认 @media（见文件头），窄屏兜底这条路径只能这样兜：
    // 一条输掉级联的 <16px 声明同样是隐患 —— 它要么是死代码，要么就是那个 bug。
    const rootPx = withStyles(() =>
      Number.parseFloat(window.getComputedStyle(document.documentElement).fontSize),
    )
    expect(rootPx, 'rem→px 的换算依据：本项目根字号应为 16px').toBe(IOS_MIN_FONT_PX)

    const offenders: string[] = []
    const unreadable: string[] = []
    for (const decl of scanFontSizeDeclarations()) {
      const hits = decl.selectors.split(',').flatMap((s) => targetsIn(s))
      if (hits.length === 0) continue
      const where =
        `${decl.file}${decl.media ? ` @ ${decl.media}` : ''} → ` +
        `${decl.selectors.trim()} { font-size: ${decl.value} }`
      const px = toPx(decl.value, rootPx)
      if (px === null) unreadable.push(where)
      else if (px < IOS_MIN_FONT_PX) offenders.push(`${where} = ${px}px`)
    }
    expect(unreadable, '护栏读不懂这些字号写法：请改写成 px/rem，或扩展本用例').toEqual([])
    expect(offenders, `聚焦 <${IOS_MIN_FONT_PX}px 的输入框时 iOS 会放大整页`).toEqual([])
  })

  it.each(TARGETS)(
    '★ $className 的生效值就是拥有它的样式表（$owner）里写的那个值',
    ({ className, owner, tag }) => {
      // 值的归属权：别的文件（尤其 responsive.css）不许再插一脚 ——
      // 插进来的那条要么输掉级联（看着生效其实没生效），要么就该搬回 owner。
      const declarations = scanFontSizeDeclarations()
      const inOwner = declarations
        .filter((d) => d.file === owner && d.media === '')
        .filter((d) => d.selectors.split(',').some((s) => targetsIn(s).some((t) => t.className === className)))
      expect(
        inOwner.length,
        `${owner} 里没有 ${className} 的 font-size：值必须写在拥有它的样式表里`,
      ).toBeGreaterThan(0)

      const declared = toPx(inOwner[inOwner.length - 1].value, IOS_MIN_FONT_PX)
      expect(declared, `${owner} 里 ${className} 的字号写法读不出来`).not.toBeNull()
      expect(computedFontSizePx(tag, className)).toBe(declared)

      const elsewhere = declarations.filter(
        (d) => d.file !== owner && d.selectors.split(',').some((s) => targetsIn(s).some((t) => t.className === className)),
      )
      expect(
        elsewhere.map((d) => `${d.file}${d.media ? ` @ ${d.media}` : ''} → ${d.selectors.trim()}`),
        `${className} 的字号只能写在 ${owner} 里：别的文件的声明会与级联顺序纠缠（正是本轮踩到的坑）`,
      ).toEqual([])
    },
  )
})
