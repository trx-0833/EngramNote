/**
 * @file 共享 Markdown 渲染工具
 * @description 提取自 NoteDetail.tsx 的模块级 marked 配置，供多个页面复用。
 * 包含 KaTeX 数学公式扩展（blockMath/inlineMath）和 highlight.js 代码高亮。
 * 渲染失败时通过 try/catch 兜底，返回带 katex-error 样式的 <span>，不抛异常。
 */
import { marked, type Tokens } from 'marked'
import { markedHighlight } from 'marked-highlight'
// 只引入 core（不含任何语言），按需注册下面显式列出的语言。
// 原实现 `import hljs from 'highlight.js'` 会打进**完整构建**（384 种语言），
// 且因为用了 hljs.highlightAuto() 而无法裁剪 —— 该 API 要求注册全部语言。
// 首屏因此被迫下载数百 KB 的高亮引擎，而这是学习笔记应用里最不常用的功能之一。
// 见 docs/overhaul-plan.md §2.8 F-7。
import hljs from 'highlight.js/lib/core'

// 按需注册语言：覆盖学习资料中最常见的代码类型
import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import csharp from 'highlight.js/lib/languages/csharp'
import css from 'highlight.js/lib/languages/css'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import kotlin from 'highlight.js/lib/languages/kotlin'
import latex from 'highlight.js/lib/languages/latex'
import matlab from 'highlight.js/lib/languages/matlab'
import plaintext from 'highlight.js/lib/languages/plaintext'
import python from 'highlight.js/lib/languages/python'
import r from 'highlight.js/lib/languages/r'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('c', c)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('c++', cpp)
hljs.registerLanguage('csharp', csharp)
hljs.registerLanguage('cs', csharp)
hljs.registerLanguage('css', css)
hljs.registerLanguage('go', go)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('kotlin', kotlin)
hljs.registerLanguage('latex', latex)
hljs.registerLanguage('tex', latex)
hljs.registerLanguage('matlab', matlab)
hljs.registerLanguage('plaintext', plaintext)
hljs.registerLanguage('text', plaintext)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('r', r)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)

import katex from 'katex'
import { sanitizeHtml } from './sanitize'

/** HTML 转义（用作无高亮时的兜底输出） */
const escapeCodeHtml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// 配置 marked 使用 highlight.js 进行代码块语法高亮
marked.use(markedHighlight({
  langPrefix: 'hljs language-',
  highlight(code: string, lang: string) {
    // 只处理已注册的语言；未注册/未标注时**返回转义纯文本**，
    // 不再调用 highlightAuto（它会强制打包全部 384 种语言）。
    const key = (lang || '').toLowerCase().trim()
    if (key && hljs.getLanguage(key)) {
      try {
        return hljs.highlight(code, { language: key }).value
      } catch (err) {
        console.warn('[markdown] 代码高亮失败，降级为纯文本:', key, err)
        return escapeCodeHtml(code)
      }
    }
    return escapeCodeHtml(code)
  },
}))

// HTML 转义工具（用于 renderKatex 失败时的原始文本展示）
const escapeHtml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/**
 * 渲染 LaTeX 公式为 HTML
 * 使用 KaTeX 渲染，失败时返回带 katex-error 样式的 <span>，不抛异常。
 *
 * @param tex - LaTeX 源文本
 * @param displayMode - 是否为块级展示模式（$$...$），否则为行内模式（$...$）
 * @returns 渲染后的 HTML 字符串
 */
export const renderKatex = (tex: string, displayMode: boolean): string => {
  try {
    return katex.renderToString(tex, {
      displayMode,
      throwOnError: false,
      errorColor: '#cc0000',
      strict: false,
    })
  } catch (e) {
    console.warn('KaTeX render failed:', e)
    return `<span class="katex-error" title="${escapeHtml(tex)}">${escapeHtml(tex)}</span>`
  }
}

// 块级公式 $$...$$ 与行内公式 $...$
// marked 自定义扩展的 renderer 接收 Token 类型，text 字段为公式内容

marked.use({
  extensions: [
    {
      name: 'blockMath',
      level: 'block',
      start(src: string) { return src.indexOf('$$') },
      tokenizer(src: string) {
        const match = /^\$\$([\s\S]+?)\$\$/.exec(src)
        if (match) {
          return { type: 'blockMath', raw: match[0], text: match[1].trim() }
        }
        return undefined
      },
      renderer(token: Tokens.Generic) {
        return `<p class="katex-block">${renderKatex(token.text, true)}</p>`
      },
    },
    {
      name: 'inlineMath',
      level: 'inline',
      start(src: string) { return src.indexOf('$') },
      tokenizer(src: string) {
        const match = /^\$([^$\n]+?)\$/.exec(src)
        if (match) {
          return { type: 'inlineMath', raw: match[0], text: match[1].trim() }
        }
        return undefined
      },
      renderer(token: Tokens.Generic) {
        return renderKatex(token.text, false)
      },
    },
  ],
})

export { marked }

/**
 * 判断某个文本节点是否应跳过公式二次渲染
 * 跳过代码块、行内代码、脚本/样式以及已经由 KaTeX 渲染过的内容。
 */
function shouldSkipMathRender(node: Node): boolean {
  let parent = node.parentElement
  while (parent) {
    const tag = parent.tagName
    if (tag === 'CODE' || tag === 'PRE' || tag === 'SCRIPT' || tag === 'STYLE') {
      return true
    }
    if (parent.classList && parent.classList.contains('katex')) {
      return true
    }
    parent = parent.parentElement
  }
  return false
}

/**
 * 在 Markdown 渲染后的 HTML 上做一次 KaTeX 二次渲染。
 *
 * marked 默认会把原始 HTML 块（例如 MinerU 输出的 <table>）原样保留，
 * 因此其中的 $...$ / $$...$$ 不会被行内扩展处理。
 * 这里通过 DOM 遍历文本节点，对未被代码块/已有 KaTeX 包裹的公式再次渲染。
 *
 * @param html - marked.parse() 之后的 HTML 字符串
 * @returns 二次渲染后的 HTML 字符串
 */
export function renderMathInHtml(html: string): string {
  if (!html || !html.includes('$')) return html
  if (typeof document === 'undefined' || typeof NodeFilter === 'undefined') return html

  const container = document.createElement('div')
  container.innerHTML = html

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  let node: Node | null = walker.nextNode()
  while (node) {
    textNodes.push(node as Text)
    node = walker.nextNode()
  }

  const mathPattern = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g

  for (const textNode of textNodes) {
    if (!textNode.data.includes('$')) continue
    if (shouldSkipMathRender(textNode)) continue

    const parent = textNode.parentNode
    if (!parent) continue

    const fragments: (Text | Node)[] = []
    let lastIndex = 0
    let match: RegExpExecArray | null

    mathPattern.lastIndex = 0
    while ((match = mathPattern.exec(textNode.data)) !== null) {
      if (match.index > lastIndex) {
        fragments.push(document.createTextNode(textNode.data.slice(lastIndex, match.index)))
      }

      const isDisplay = match[1] !== undefined
      const tex = (isDisplay ? match[1] : match[2]).trim()
      const rendered = renderKatex(tex, isDisplay)

      const template = document.createElement('template')
      template.innerHTML = rendered
      fragments.push(template.content.cloneNode(true))

      lastIndex = match.index + match[0].length
    }

    if (fragments.length === 0) continue

    if (lastIndex < textNode.data.length) {
      fragments.push(document.createTextNode(textNode.data.slice(lastIndex)))
    }

    for (const fragment of fragments) {
      parent.insertBefore(fragment, textNode)
    }
    parent.removeChild(textNode)
  }

  return container.innerHTML
}

/**
 * 将 Markdown 文本渲染为 HTML 字符串
 * 内部使用已配置 KaTeX 扩展和代码高亮的 marked 实例。
 * 管道顺序：marked.parse → sanitizeHtml（DOMPurify 白名单消毒）→ renderMathInHtml（KaTeX 二次渲染）。
 * 空字符串或假值返回空字符串。
 *
 * **容错说明（见 docs/overhaul-plan.md §2.8 F-2）**：
 * 本函数在**渲染阶段同步执行**，任何一处抛错都会在 React 渲染期冒泡，
 * 导致整棵组件树被卸载（整站白屏，且刷新后复现）。
 * 因此这里整体包一层 try/catch：渲染失败时降级为**转义后的纯文本**，
 * 让用户至少还能读到内容，而不是面对一个白屏。
 *
 * @param text - Markdown 源文本
 * @returns 渲染后的 HTML 字符串（失败时为转义纯文本）
 */
export function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    const parsed = marked.parse(text) as string
    return renderMathInHtml(sanitizeHtml(parsed))
  } catch (err) {
    console.error('[markdown] 渲染失败，降级为纯文本:', err)
    return renderPlainTextFallback(text)
  }
}

/**
 * 渲染失败时的降级输出：转义 HTML 后按空白保留展示
 *
 * 不引入额外依赖：手写最小转义，避免"兜底路径再抛错"。
 */
function renderPlainTextFallback(text: string): string {
  const escaped = String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
  return `<pre style="white-space:pre-wrap;word-break:break-word">${escaped}</pre>`
}
